# P6.5 Windows desktop distribution

## Status

P6.5 assembles a per-user Windows desktop product from source-owned components.
It does not change the authority of the P6 personal product, enable an
enterprise Runtime, or make optional infrastructure a prerequisite.

The release target is one Authenticode-signed WiX Burn EXE. Until the complete
product journey, clean-Windows installer lifecycle and signature checks pass,
all generated EXE/MSI files are unsigned engineering test artifacts and
`production_ready=false`.

The 2026-08-19 unsigned engineering artifact passed guarded install, upgrade,
downgrade rejection, transaction rollback, uninstall/data-retention and closed
runtime first-launch checks in offline Windows Sandbox runs under a fresh,
non-elevated standard user. The native window, loopback process topology,
health/readiness, backend authorization boundary, challenge-HMAC proof, SQLite
creation and graceful shutdown were observed directly. This closes those
engineering gates only; the artifact was built from `engineering-dirty` source,
remains unsigned, and still lacks the required personal product journeys.

## Process topology

```text
OmniBase.exe (Electron, one instance)
  -> verifies resources/runtime/runtime-manifest.json and every declared digest
  -> creates one 32-byte random native proof key
  -> starts OmniBase.RuntimeHost.exe with a closed environment

OmniBase.RuntimeHost.exe
  -> verifies runtime-host.json and the backend/Node/Next entrypoint digests
  -> creates a separate 32-byte random authorization token
  -> creates a kill-on-close Windows Job Object
  -> starts backend with the authorization token and native proof key
  -> starts Next with the authorization token, but never the proof key

Electron readiness
  -> GET Next /health with a fresh 64-hex challenge
  -> Next injects the server-only authorization token into the backend request
  -> backend health must succeed
  -> backend returns HMAC-SHA256(native_proof_key, challenge)
  -> Next forwards that proof without generating or replacing it
  -> Electron verifies the proof in constant time before opening the window
```

Electron loads only `http://127.0.0.1:3000`. Navigation, popup, permission and
IPC surfaces remain closed. RuntimeHost accepts no argv of its own, launches
children without a shell, uses fixed argument arrays and a closed environment,
and bounds startup, shutdown and captured output.

## Desktop identity boundary

`OMNIBASE_DESKTOP_NATIVE_PROOF_KEY` and
`OMNIBASE_DESKTOP_INSTANCE_TOKEN` are independent 64-lowercase-hex values.
Electron creates the proof key and passes it only to RuntimeHost; RuntimeHost
passes it only to the backend. RuntimeHost creates the authorization token and
passes it to Next and the backend. Electron and Next cannot generate a backend
proof, while the backend never receives Electron's Browser surface.

Browser callers cannot supply this identity. The Next proxy removes
`x-omnibase-desktop-instance`, `x-omnibase-desktop-challenge` and
`x-omnibase-desktop-proof` from Browser requests and removes the same headers
from ordinary upstream responses. It then injects the trusted authorization
token on the server-side backend hop. Only `/health` forwards a canonical
challenge and a proof returned by the successfully authenticated backend.

Neither secret may appear in argv, Browser JavaScript, local storage, response
bodies, logs, diagnostics, manifests or installer authoring.

## Local storage

The personal desktop service is a separate FastAPI composition under
`backend/src/omnibase/desktop_local`. It does not import the PostgreSQL
application settings, parse dotenv files or inspect provider/database ambient
configuration. It binds only IPv4 loopback and requires the exact instance
header on every route.

The default user data root is:

```text
%LOCALAPPDATA%\OmniBase
```

SQLite state is created below that root. The schema uses `STRICT` tables,
foreign keys, WAL/full synchronous durability, migration checksums, one Owner,
append-only audit triggers and closed runtime-job transitions. Installer
authoring never owns or removes this tree.

The current desktop-local Browser surface contains health/readiness and Owner
bootstrap/status only. Existing P6 Browser journeys that still depend on the
PostgreSQL/Redis/MinIO application are not silently emulated. A packaged shell
is not a usable OmniBase 1.0.0 release until every required personal journey is
explicitly wired and accepted.

## Build layers

1. Build the Next production standalone server and copy its static/public
   assets into the standalone tree.
2. Freeze the minimal desktop-local backend with the pinned Python 3.12 x64
   PyInstaller environment. Optional PostgreSQL, ML, Docker and Sandbox
   dependencies are excluded.
3. Publish RuntimeHost as a self-contained, single-file `win-x64` executable
   with the pinned .NET 8 SDK.
4. Compile the Electron source once to validate it, assemble the closed runtime
   payload, inject the canonical runtime-manifest SHA-256 into a copied desktop
   source tree, and compile that copied source.
5. Package Electron 43.4.0 with `@electron/packager` 20.3.0. The application
   source is ASAR-packed and the verified runtime tree is copied to
   `resources/runtime`.
6. Validate and digest the packaged application directory, copy exactly those
   files into a new exclusive bind tree, and make WiX Toolset 7 bind only that
   tree as one embedded per-user MSI inside one Burn EXE. Revalidate the bind
   tree after the build.
7. Promote WiX's hard-linked build outputs into independent single-link release
   files and emit a digest-bound build report. Signing is a separate trusted
   stage and no key material enters this repository.

Build outputs live outside the repository. Generated `.next`, `node_modules`,
virtualenv, PyInstaller, .NET, Electron, MSI and EXE content is not source truth.
Failed staging trees are retained for inspection rather than recursively
deleted.

## Installer contract

- scope: current user, no UAC or machine fallback;
- install root: `%LOCALAPPDATA%\Programs\OmniBase`;
- data root: `%LOCALAPPDATA%\OmniBase`;
- one Burn EXE containing one embedded MSI;
- MSI-native major upgrades and explicit downgrade rejection;
- normal uninstall removes installer-owned application files and shortcuts but
  retains user data;
- no Docker, WSL, PostgreSQL/pgvector, BGE-M3 or hardened Sandbox prerequisite.

## Release gates

A distributable 1.0.0 claim requires all of the following direct evidence:

- clean declared source commit and source-complete dependency locks;
- backend, frontend, Electron, RuntimeHost, payload and installer contract
  tests;
- successful production Next build, backend freeze, RuntimeHost publish,
  Electron package and WiX Burn build;
- launch proof that the closed runtime reaches challenge-HMAC readiness and
  required personal product journeys work;
- guarded clean install, upgrade, downgrade rejection, failed-upgrade rollback
  and uninstall with retained user data;
- Authenticode signing and post-signature verification on the distributed EXE
  and required executable payloads.

Missing certificates, source/lock evidence, required product routes or any
unpassed clean-target gate are release vetoes. They do not authorize
Docker/WSL/Hyper-V repair, virtual-disk mutation, security-check removal or a
production-ready label.
