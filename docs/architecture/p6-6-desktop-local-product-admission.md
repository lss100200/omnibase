# P6.6 desktop-local product admission

## Status

P6.6 is an engineering R0 product-admission increment over the P6.5 Windows
desktop distribution boundary. It makes the packaged shell usable for one
bounded offline journey:

1. establish the single local Owner without email, password or JWT;
2. create, list and archive local Workspaces;
3. restart and recover the same Owner and Workspace rows from SQLite;
4. retain append-only audit evidence for every mutation.

This is not completion of the required personal product journeys. Agent
invocation, Provider credentials, RAG, documents, ChangeSets, Skills, MCP,
Planner, Multi-Agent and PostgreSQL application routes remain unavailable.
P6.6 does not change the unsigned, non-distributable status of the current
Windows engineering artifact.

## Authority boundary

P6.6 does not turn the RuntimeHost authorization token into a user credential.
The three launch identities have separate purposes:

- Electron creates a native proof key. The backend uses it only to produce the
  challenge-HMAC readiness proof.
- Electron creates a native control token. Electron main and the backend use it
  only for the closed Owner and Workspace control API.
- RuntimeHost creates an authorization token. Next and the backend use it only
  to authenticate the server-side proxy hop.

All three values are independent 32-byte CSPRNG values encoded as 64 lowercase
hexadecimal characters. They exist only for one launch. They are passed in
closed child-process environments, never argv. The native proof key and native
control token are passed to the backend but not Next. The authorization token
is passed to Next and the backend. No launch identity enters renderer
JavaScript, local storage, SQLite, response bodies, logs, manifests, installer
authoring or evidence receipts.

## Why mutations use IPC

The Next `/api/v1/[...path]` handler is a server-side proxy. In desktop mode it
possesses the RuntimeHost authorization token and can authenticate a backend
hop for any loopback caller. Publishing Owner or Workspace routes through that
catch-all would make Next a confused deputy and disclose or mutate product data
for a local HTTP client that knows no backend identity.

P6.6 therefore uses two separate surfaces:

- Next may proxy only `GET /health/ready`; `/health` has its dedicated
  challenge-aware handler. Its `/api/v1` catch-all is closed in desktop mode.
- Owner status/bootstrap and Workspace reads/mutations are available only below
  `/desktop/v1` on the backend. Electron main calls that backend origin
  directly with the native control token after runtime readiness.

The renderer invokes eight exact IPC channels: application version, runtime
status/retry, Owner status/bootstrap, and Workspace list/create/archive. The
preload exports typed methods only. Main validates the sender origin, exact
argument keys, identifier grammar, row-version bounds, string length and
control characters. The native client independently validates the backend
origin, token, response byte budget, 256-row Workspace bound, unique identifiers
and every response DTO before returning a secret-free result to the renderer.

Browser-supplied instance, challenge, proof and native-control headers are
removed by Next. Desktop proxy mode also drops Browser `Authorization` and
`Cookie`, rejects query-bearing or non-health paths before contacting an
upstream, and rejects any target that is not an explicit HTTP IPv4-loopback
origin with a nonzero port.

## Desktop-local API

The instance-authenticated surface remains:

- `GET /health`;
- `GET /health/ready`;

The native-control surface is:

- `GET /desktop/v1/owner`;
- `POST /desktop/v1/owner/bootstrap`;
- `GET /desktop/v1/workspaces`;
- `POST /desktop/v1/workspaces`;
- `POST /desktop/v1/workspaces/{workspace_id}/archive`.

Native routes reject missing, malformed, duplicate or wrong control tokens and
reject mixed instance/challenge/proof identity. Ordinary routes reject a native
control header. The former Browser-accessible
`GET /api/v1/owner` and `POST /api/v1/owner/bootstrap` routes are removed.
Unknown PostgreSQL, RAG, Provider, Runtime and Sandbox paths continue to return
the stable redacted desktop 404 after their applicable transport identity
check.

## SQLite transactions

P6.6 reuses desktop schema version 1; it does not add or modify a migration.
The existing Owner, Workspace and audit tables already have the required
foreign keys, `STRICT` constraints, append-only audit triggers and one-way
`active -> archived` Workspace transition.

Owner bootstrap remains singleton and idempotent. The Owner insert and
`owner_bootstrapped` audit event commit in one `BEGIN IMMEDIATE` transaction.
A replay returns the existing Owner and cannot rename it or append another
event.

Workspace creation requires the Owner, is limited to 256 total Workspaces, and
commits the row with a `workspace_created` event in one transaction. Audit
payloads contain state and row-version metadata, not the user-supplied name.

Workspace archive requires the exact Owner, identifier, active state and
expected row version. It increments `row_version` and appends
`workspace_archived` in the same transaction. Missing rows return 404; stale or
already archived rows return a stable conflict. An audit failure rolls back the
state and row-version update. P6.6 intentionally has no restore operation.

## Renderer journey

The root page detects the preload bridge and routes the Electron renderer to
`/desktop` before applying the Browser JWT redirect. Direct web access to
`/desktop` has no HTTP fallback.

The first-run page asks only for a display name and first Workspace name. It
does not create or persist access/refresh tokens. A completed Owner with no
Workspace is recoverable: the Workspace creation form remains available after
restart. The admitted page lists active and archived Workspaces, requires
confirmation before archive, displays row-version/update metadata and states
that Agent, Provider, RAG and MCP remain unavailable.

The existing JWT dashboard and PostgreSQL DTOs are not emulated. In particular,
the desktop Workspace shape does not invent templates, memberships, Runs,
quotas or tenant authority.

## Verification

The required offline gate is:

- desktop-local pytest plus Ruff check/format;
- Electron tests, typecheck and build, including the exact IPC set, native
  client DTO validation, token separation and loopback target checks;
- RuntimeHost tests proving the native control token reaches only the backend;
- frontend tests, typecheck, lint and production build, including closed proxy
  catalog and bridge detection;
- desktop backend freeze/release contract tests and maintainer map validators.

A new unsigned build may be created outside the repository after these checks.
Installer lifecycle and Authenticode acceptance are not rerun or inferred by
this source increment. A clean Windows product-journey run is still required
before any distributable or installable-and-usable 1.0.0 claim.

## Failure recovery

Stop the Electron shell so RuntimeHost closes its Job Object and invalidates all
three launch identities. Preserve `%LOCALAPPDATA%\OmniBase`; do not delete or
rewrite SQLite to repair a product error. Forward-fix application bytes. A
failed Owner or Workspace transaction must leave no partial row or audit event.
Do not start Docker, WSL, Hyper-V, PostgreSQL or a Provider to compensate for an
unsupported desktop route.
