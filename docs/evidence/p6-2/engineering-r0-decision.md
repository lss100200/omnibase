# P6.2 personal capability center engineering R0 decision

Status: `P6_2_PERSONAL_CAPABILITY_CENTER_ENGINEERING_COMPLETE`

## Implemented

- Chinese personal capability center combining the six first-party Skills,
  ten-role model posture, exact three-tool read-only MCP boundary and an
  Owner-triggered scan-only local Skill preflight;
- same-session bounded terminal history compilation with deterministic newest
  retention and a non-authoritative browser-local fingerprint;
- tenant/Workspace-scoped bounded ChangeSet journal with full restored-record
  structural and scope validation before the existing CAS/three-way rollback;
- self-contained Windows Companion source with exact ZIP and installed-payload
  integrity verification, atomic install, CSPRNG config initialization and
  offline read-only doctor.

## Forward review findings

An actual .NET build, using the already preserved and officially SHA-512
verified .NET SDK `8.0.424`, found that the first Companion source omitted
required `System`/collection/I/O/LINQ/threading namespaces and could not compile.
Comparison with the P6.1 installer also found that the first expansion had
weakened the prior exact manifest schema, manifest-size, compression-ratio,
source-commit and posture checks. The forward fix restores all of those checks,
adds exact installed-directory integrity verification, makes doctor arguments
closed, validates the exact image repositories and config key set, and requires
all four feature gates to be explicitly false.

The first complete frontend run also exposed a journal predicate bug: passing
the two-argument scope validator directly to `Array.filter` caused the array
index to be interpreted as an expected scope. The predicate is now wrapped
explicitly, and restored records additionally reject scope mismatch, traversal,
duplicate paths, invalid file-version shapes, oversized text and malformed
digests.

## Proven boundaries

- local Skill discovery performs no execution, installation or network use;
- unknown local Skills remain `unsupported_unreviewed` or `rejected` and are
  never written to first-party migration `0014`;
- bounded history uses only redacted terminal messages from the selected
  session and does not create replay authority;
- ChangeSet recovery remains browser-local and cannot reconstruct file handles
  or create a server write API;
- MCP remains independent of Agent Alpha and `no_tool` is unchanged;
- migration head remains `0016`; no migration `0017` is introduced;
- Runtime, Planner, Multi-Agent and MCP Runtime remain disabled;
- the Windows project compiles with zero errors and zero warnings under the
  preserved .NET SDK after the forward fix;
- Release verification remains `production_ready=false`, unsigned and
  exact-digest-only.

## Not proven / not claimed

- no arbitrary third-party Skill is approved, installed or executed;
- MCP is not connected to Agent Alpha and no arbitrary server is registered;
- conversations are not synchronized across devices;
- no Agent automatically writes a user file;
- real OCI image digests are not published and no `docker compose pull/up` was
  performed;
- Authenticode/publisher identity, clean Windows VM acceptance and production
  deployment remain unproven;
- engineering completion is not enterprise P34.7 approval or production
  activation.

## Final verification

- frontend unit regression: `169 passed`; TypeScript typecheck, Next lint,
  explicit-path Prettier and production build all passed; the build generated
  17 routes;
- focused backend model/Skill/MCP/Agent regression: `85 passed` without Docker
  or a business database;
- P5.1A/P5.2A/P5.3A sealed-contract regression after the ordered raw-byte
  reseal: `129 + 200 + 78 = 407 passed`;
- Windows Release regression: `14 passed, 1 skipped` (the existing
  POSIX-only fsmonitor fixture is skipped on Windows);
- .NET SDK `8.0.424` Release build: `0 warnings, 0 errors`; canonical formatter
  check passed;
- self-contained Companion artifact:
  `E:\Agent IDE\Artifacts\OmniBase-P62-Companion-R0\OmniBase.Setup.exe`,
  `67,519,558` bytes,
  SHA-256 `1669acef98afcd8aa865593d541f7e9915d934cad3714bf8b3313b46f869936d`,
  Authenticode `NotSigned`;
- maintainer map: `66 invariants`, `48 modules`, `1014 path specs`,
  `3098 matched files`, `324 entrypoints`, `20 discovered HTTP entrypoints`
  and `264 verification commands`;
- maintainer benchmark: `3 plans`, `8 scenarios`, `6 critical scenarios` and
  `9 unsafe vetoes`.

## Safety facts

- root `.env` was not read;
- no business database was accessed or migrated;
- Docker, WSL and VHDX were not started, modified or cleaned;
- no API key or private key was generated for production or transmitted;
- no push, PR, merge or deploy was performed.

The clean-HEAD verification and final local commit are recorded in the handover
section for this branch. No external publication or activation is implied.
