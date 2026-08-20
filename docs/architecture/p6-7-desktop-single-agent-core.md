# P6.7 personal desktop single-agent core

## Status

P6.7 is an engineering R0 increment over P6.6 product admission. It makes one
additional local journey available without Docker, WSL or PostgreSQL:

1. the single Owner can configure their own model Provider;
2. each Workspace has one parent Agent with no tools, files, MCP, Skills or
   child agents;
3. conversations persist in SQLite, stream token-by-token, and can be stopped,
   retried as a new invocation, and recovered after restart without replaying
   cancelled or unknown calls.

This is not OmniBase 1.0.0, Authenticode, or a production-ready claim. Files,
RAG, citations, ChangeSet, Skills, MCP, multi-agent, PostgreSQL, pgvector and
enterprise Trust Policy remain closed.

## Why a desktop adapter instead of Model Gateway imports

The frozen desktop backend excludes `openai`, `httpx`, `cryptography` and
PostgreSQL Settings. P6.7 therefore ports the Model Gateway family grammar
(name-first, URL as an auxiliary hint, gears, thinking depth, token budgets)
into `omnibase.desktop_local.family` and talks HTTPS with the standard library.
Unrecognized model names become `generic-openai-compatible`; they are not
hard-rejected. This is not a second product stack: it is the smallest
fail-closed adapter that still reaches a user-configured endpoint from the
frozen EXE.

## Authority boundary

INV-082/INV-083 three-secret model is unchanged:

- native proof: challenge-HMAC readiness only;
- native control: Electron-main-to-backend `/desktop/v1` only;
- authorization token: Next/backend proxy hop only.

Provider, Agent and conversation mutations do not go through the Next catch-all.
The renderer never receives launch identities, encrypted blobs, fingerprints or
raw API keys. Vault GET exists only for Electron main. Sending a message is
personal approval by the local Owner; there is no enterprise Trust Policy UI.

## Secret vault

Electron main encrypts with `safeStorage` (Windows DPAPI). SQLite stores:

- `credential_reference` (`electron-safe-storage:v1`);
- `encrypted_secret_blob`;
- `secret_fingerprint` (SHA-256 hex of the plaintext, not reversible).

Plaintext keys, `Authorization` and Bearer values are never written to SQLite,
logs, argv, handover, manifests or screenshots. The backend never decrypts. For
test and send, main decrypts in process and posts `secret` only on the native
control hop.

## Network

Remote Providers require HTTPS. HTTP is allowed only for `127.0.0.1`,
`localhost` and `::1` when the user explicitly enables loopback HTTP. LAN and
other private-network targets are rejected. URL userinfo and query credentials
are rejected and stripped from errors. After DNS validation the client pins
TCP to the already-validated public IP set; TLS SNI and `Host` stay the
original hostname. Provider test has timeout, cancel and a response-size cap,
and requires usable assistant `choices` rather than any HTTP 200 JSON object.
Streaming SSE requires explicit Provider terminal proof; truncated or
disconnected streams durable-terminalize as `unknown` (or `cancelled`). Cancel
accept is the CAS winner against success. Conversation events are scoped by
workspace and conversation identity.

## Native-control surface added by P6.7

P6.6 Owner/Workspace routes remain. P6.7 adds:

- `GET /desktop/v1/workspaces/{id}/agent`;
- `GET/POST /desktop/v1/providers`;
- `DELETE /desktop/v1/providers/{id}`;
- `GET /desktop/v1/providers/{id}/vault` (Electron main only);
- `POST /desktop/v1/providers/{id}/test`;
- conversation list/create/archive/get;
- `POST .../messages` as SSE;
- `POST /desktop/v1/invocations/{id}/cancel`.

Streaming is consumed by Electron main and forwarded on
`omnibase:conversation:event`. Next does not buffer or rewrite the stream.

## SQLite

Schema version 2 is applied by `desktop_0002_provider_conversation`. This is a
desktop-namespace migration, not Alembic 0013/0017. Existing Workspaces receive
one parent Agent on upgrade. Conversation archive is one-way. Interrupted
invocations become `unknown` on startup and are not auto-replayed.

## Renderer journey

`/desktop` remains the only desktop product surface. After Owner admission the
workbench lets the user configure a Provider, create/list/switch/archive
sessions, stream, stop (`生成已停止` / `调用已取消`), and retry as a new
invocation while keeping the failed record. A live invocation keeps a global
Stop after Workspace/Conversation switch; returning to the origin scope
restores running/Stop and parked live text. Send, retry and list-detail
completions apply only when the captured scope generation still matches, so
a stale completion from scope A cannot overwrite workspace/conversation B.
Requested/actual model, Provider
name, status, duration, tokens when provided, thinking depth and redacted
errors are folded, not dumped. Cost is omitted unless a reliable rate exists
(P6.7 does not invent rates). Chinese-first copy follows system language;
body text is at least 15–16px.

## Verification

Focused gates: desktop-local pytest including provider/conversation tests,
Ruff, Electron tests/typecheck, frontend tests/typecheck/lint, RuntimeHost
with the pinned SDK, and both maintainer validators. Live paid Provider calls
with user secrets are not required; unit tests use isolation keys and a
loopback fake OpenAI-compatible server.

Unsigned engineering rebuild is allowed after those gates. Authenticode,
Windows Sandbox UI and a live paid Provider remain unproven.

## Failure recovery

Stop Electron so RuntimeHost reaps the child group and invalidates launch
identities. Preserve `%LOCALAPPDATA%\OmniBase`. Forward-fix application bytes.
Do not decrypt SQLite blobs in the renderer, publish mutating Next routes,
import PostgreSQL Settings into the freeze, or start Docker/WSL/PostgreSQL to
complete this journey.
