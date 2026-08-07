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
they do not become a positive capability. A hostname is not network evidence;
the default network state is `unknown` unless an explicitly supplied closed-set
value (`available`/`unavailable`/`unknown`) is configured by the caller.

## Desktop lifecycle

`scripts/runtime/omnibase_desktop.py` is a bounded, allowlisted lifecycle
wrapper over repository Compose configuration:

```text
doctor / capabilities          probe host capabilities (JSON report)
ports                          advisory port availability
ports-suggest <port>           advisory free-port suggestion
start --profile lite|local     start allowlisted services (detached)
status                         compose ps for allowlisted services
health                         advisory capability + port + service health
logs --tail N                  bounded, redacted compose logs
stop                           stop allowlisted services
```

- Only `lite|local` profiles are accepted; `start --profile hardened` is
  rejected. Hardened start support is never claimed.
- Services and Compose verbs are closed-set allowlists; commands are always
  argument arrays passed directly to `subprocess` (never shell strings built
  from user input).
- Every Compose verb explicitly passes `--env-file .env.example`; the root
  `.env` is never read or expanded.
- Status/health/log output passes through the safe diagnostic redactor.
- Port detection is advisory: startup must handle bind failure explicitly
  rather than claiming a reservation.

## Diagnostics privacy

Diagnostic payloads contain capability facts, service states, exit codes, safe
configuration shape, and explicit privacy flags. Secret-like keys are replaced
with `[REDACTED]`, recursively through mappings, lists and tuples, with
case-insensitive sensitive-key matching (authorization, cookie/set-cookie, api
key/token/secret/password/private-key/credential variants and repository
provider credential names). Redaction bounds: maximum depth 8, maximum
collection size 256, maximum rendered string length 2048; cycles are replaced
with a deterministic `[CYCLE]` marker. The bundle must not include `.env`,
credentials, tokens, Authorization headers, cookies, provider responses, or
user documents.

Scalar strings additionally pass through a bounded, deterministic line
tokenizer that removes credentials without relying on keyword-bearing samples:

- URI/DSN userinfo passwords for any scheme (`scheme://user:password@host` and
  `%3A`-encoded variants);
- sensitive query keys and fragments (`key`, `api_key`, `token`,
  `access_token`, `signature`, `sig`, `credential`, `password` and provider
  variants) such as `?key=abc` / `#token=abc`;
- `NAME=value` assignments, CLI `--name=value` forms, `Name: value` headers
  and quoted JSON-ish log lines, all with the same normalized sensitive-name
  policy;
- provider-key shapes are covered through the value of a sensitive name, never
  through guessing secret prefixes.

All parsing is linear and bounded (string capped at 2048 characters, at most
512 lines, names/values length-capped); a keyword-marker check remains as a
deterministic fail-closed fallback. `LifecycleResult` stdout/stderr,
status/health/log text, exception text and serialized diagnostics all pass
through this protection.

The attack matrix (including opaque secrets with no token/secret/password
keyword) is in `backend/tests/test_runtime_redaction_attacks.py`; focused
lifecycle wrapper tests (exact argument arrays with explicit
`--env-file .env.example`, no shell, allowlists, Hardened rejection, timeout
and executable-not-found behavior, bounded/redacted output, bind-failure
propagation, `logs --tail` bounds, status/health failure behavior, Windows
paths without command injection, root `.env` never selected) are in
`backend/tests/test_runtime_lifecycle.py`.

## Capability schema and provenance

Every reported fact carries a source/provenance and an evidence state:

| Field | Provenance | Evidence states |
|---|---|---|
| os, architecture | `platform` module | detected / unknown |
| memory_bytes | `os.sysconf` probe | detected / unknown |
| disk_free_bytes | `shutil.disk_usage` probe | detected / unknown |
| gpu | bounded `nvidia-smi` probe or Apple Silicon platform probe | available / detected / unavailable / unknown / not_applicable |
| container_engine | `shutil.which` for docker/podman | detected / unknown (presence is not isolation proof) |
| network | caller-supplied closed set only | configured / unknown (hostname is not evidence) |
| ports | local `connect_ex` probe | detected / unavailable / not_applicable (advisory only) |
| modes/backends | derived from probe facts | never claim more than proven |

## Platform evidence matrix

Only the current tested host is directly verified. Windows/macOS/Linux,
x86_64/ARM64, NVIDIA/CUDA, Apple MPS and container variants not run on this
host remain `not_proven`; evidence from one host is never generalized to
another platform. See the `platform_matrix` field of the capability report.

## RAG performance profiles

`omnibase.rag.performance` provides bounded CPU, CUDA, and MPS profiles. Low or
unknown resources select `lite-cpu`. Warmup results distinguish embedding
readiness from reranker readiness; a missing reranker reports `fallback_rrf`
and keeps retrieval usable rather than claiming reranking succeeded.
