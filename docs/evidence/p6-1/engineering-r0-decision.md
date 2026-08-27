# P6.1 A-D engineering R0 decision

Status: `P6_1_ENGINEERING_COMPLETE_RELEASE_PREVIEW_VERIFIED`

Implemented:

- authenticated six-Skill native catalog and exact Owner/Workspace/Agent install/disable UI;
- model-name-first DeepSeek/GPT adaptation and cache/reasoning usage projection;
- explicitly launched three-tool read-only stdio MCP preview;
- deterministic Windows ZIP builder, offline immutable-image preflight,
  personal-lifecycle-aligned no-build release Compose and EXE verifier/extractor source.

Safety facts:

- migration head remains `0016`;
- MCP is not connected to Agent Alpha and `no_tool` keeps its old meaning;
- Runtime, Planner and Multi-Agent defaults are unchanged;
- enterprise P34.7 remains frozen;
- root `.env` and business database were not accessed;
- no VHDX mutation, Docker build, push, PR, merge or deployment occurred.

A portable Microsoft .NET SDK 8.0.424 archive was recovered outside the
repository (`285090820` bytes) and its SHA-512 exactly matches the official
release value
`1787ab90635c2950672ed7c6507b000e1b212ea7d9a22fcef37061344d37c64d4c4eda12b8742601eff5b45c8736485b31c55613892f240c300190e4e88a58b0`.
The recovered SDK reports SDK `8.0.424`, MSBuild `17.11.48` and Windows x64.

Authoritative preview artifacts were built outside the repository from clean
source commit `cf707e28c656606114bee0aa69bde979af3758a2`:

- ZIP: `E:\Agent IDE\OmniBase-P61-Release-cf707e2\OmniBase-v1.0.0-preview-windows-x64.zip`,
  `27131` bytes, SHA-256
  `e9a4049d5d9986a9cc76cf5a4b3a67f17587135f3de7c08554979de652c89fd3`;
- EXE: `E:\Agent IDE\OmniBase-P61-Release-cf707e2\publish\OmniBase.Setup.exe`,
  `166920` bytes, SHA-256
  `6b0c904f29239a474c9dbfe7e0e382b3c5e20341aa637b71cf5db58168b6896c`.

Two independent builds of each artifact were byte-identical. Authenticode is
`NotSigned`, so `publisher_signature_verified=false` and
`authenticode_verified=false` remain mandatory. The manifest also keeps
`production_ready=false` and `vhdx_mutation_allowed=false`.

The real EXE completed 20/20 fresh-target installations. Each target contained
the exact six-file closed set and no staging residue. Six binary negative cases
(payload digest tamper, extra archive file, forged `production_ready=true`,
path traversal, corrupt ZIP and pre-existing target) all returned exit `2`,
left no partial target or staging directory, and did not escape the selected
test root.

Independent review fixes tenant-scoped native Skill identities and lifecycle
evidence, conservative GPT model grammar/Chat Completions parameters, Browser
cache telemetry, MCP stable-object and bounded-I/O checks, metadata-only Git
inspection, and first-install release lifecycle. Official OpenAI documentation
was fetched on 2026-08-14 and confirms that reasoning effort should be tuned to
task difficulty, that reasoning models perform better with Responses while
Chat Completions remains supported, and that verbosity is a Responses text
control. OCI publication, publisher signing, Authenticode verification and a
production deployment remain not proven.
