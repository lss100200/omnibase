# Desktop runtime modes and diagnostics

The local desktop contract is provider-neutral and fail-closed. It reports
observable host facts without treating Docker Desktop, WSL, or Podman as proof
of hostile-code isolation.

## Modes

- **Lite**: always available when the host probe can run. Uses remote/cloud
  model providers or read-only RAG; it does not require a local container
  engine.
- **Local**: available only when Docker or Podman is observable. It describes
  local service orchestration, not a production Sandbox.
- **Hardened**: locked unless independent P34.7/P34.5 target-host evidence is
  injected and verified. The desktop probe never enables this mode.

Unknown memory, disk, GPU, virtualization, or network facts remain unknown;
they do not become a positive capability. `scripts/runtime/omnibase_desktop.py`
prints a JSON capability report, checks ports, suggests a bounded replacement
port, and returns exit code 2 when a requested mode is unavailable.

## Diagnostics privacy

Diagnostic payloads contain capability facts, service states, exit codes, safe
configuration shape, and explicit privacy flags. Secret-like keys are replaced
with `[REDACTED]`. The bundle must not include `.env`, credentials, tokens,
Authorization headers, cookies, provider responses, or user documents.

## RAG performance profiles

`omnibase.rag.performance` provides bounded CPU, CUDA, and MPS profiles. Low or
unknown resources select `lite-cpu`. Warmup results distinguish embedding
readiness from reranker readiness; a missing reranker reports `fallback_rrf`
and keeps retrieval usable rather than claiming reranking succeeded.
