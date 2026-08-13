# P6.1 A-D engineering R0 decision

Status: `P6_1_ENGINEERING_REVIEW_FIX_IMPLEMENTED_PENDING_FINAL_VERIFICATION`

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
The final EXE remains pending until this recovery source is committed and a
clean HEAD can supply the manifest-bound source commit.

Independent review fixes tenant-scoped native Skill identities and lifecycle
evidence, conservative GPT model grammar/Chat Completions parameters, Browser
cache telemetry, MCP stable-object and bounded-I/O checks, metadata-only Git
inspection, and first-install release lifecycle. Publisher signature,
Authenticode, OCI publication and real Windows installation remain not proven.
