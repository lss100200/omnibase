# Desktop runtime modes and diagnostics

The local desktop contract is provider-neutral and fail-closed. It reports
observable host facts without treating Docker Desktop, WSL, or Podman as proof
of hostile-code isolation.

## Modes

- **Lite**: always available when the host probe can run. Uses remote/cloud
  model providers or read-only RAG; it does not require a local container
  engine.
- **Local**: available only when a bounded, `shell=False`, short-timeout
  `docker compose version` / `podman compose version` probe exited 0 through
  the shared container-engine resolution contract (Docker first, then Podman).
  Executable presence alone is `detected/not_proven` and never claims Local.
  A Podman-only host claims Local only because the lifecycle actually executes
  a controlled `podman compose --env-file .env.example` path.
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
- The container engine comes from the same `resolve_container_engine` contract
  the capability probe uses: `docker compose` when the Docker compose provider
  probe exits 0, a controlled `podman compose` path when only Podman is
  verified, and fail-closed `container_engine_not_found` when neither probe
  exits 0 (executable presence alone is never a compose provider).
- Every Compose verb explicitly passes `--env-file .env.example`; the root
  `.env` is never read or expanded.
- Status/health/log output passes through the safe diagnostic redactor.
- Port detection is advisory: startup must handle bind failure explicitly
  rather than claiming a reservation.

## Diagnostics privacy

Diagnostic payloads contain capability facts, service states, exit codes, safe
configuration shape, and explicit privacy flags. Secret-like keys are replaced
with `[REDACTED]`, recursively through mappings, lists and tuples, with a
normalized sensitive-name policy: a token/full-field closed set plus a bounded
`_`-delimited suffix policy (authorization, cookie/set-cookie, api
key/token/secret/password/private-key/credential variants and repository
provider credential names). Keys are tokenized at camelCase/PascalCase case
boundaries (`stripeApiKey` -> `stripe_api_key`, `azureAccessToken` ->
`azure_access_token`, `openAiApiKey` -> `open_ai_api_key`), and the `_key`
suffix rule is narrow: `sort_key`, `cache_key`, `foreign_key`,
`keyboard_layout` and `monkey` are preserved while `api_key`, `secret_key`,
`access_key`, `signing_key`, `private_key`, `encryption_key` and provider
variants are redacted. There is deliberately **no arbitrary substring
matching**. Redaction bounds: maximum depth 8, maximum collection size 256,
maximum rendered string length 2048; cycles are replaced with a deterministic
`[CYCLE]` marker. The bundle must not include `.env`, credentials, tokens,
Authorization headers, cookies, provider responses, or user documents.

Scalar strings additionally pass through a bounded, deterministic line
tokenizer that removes credentials without relying on keyword-bearing samples:

- URI/DSN userinfo passwords for any scheme (`scheme://user:password@host` and
  `%3A`-encoded variants);
- sensitive query keys and fragments (`key`, `api_key`, `token`,
  `access_token`, `signature`, `sig`, `credential`, `password` and provider
  variants) such as `?key=abc` / `#token=abc`;
- `NAME=value` assignments with **any bounded horizontal whitespace**
  (`NAME = value`, including wide runs — "more than 8 spaces" never passes),
  CLI `--name=value` / `--name = value` forms, `Name: value` / `Name : value`
  headers and quoted JSON-ish log lines, all with the same normalized
  sensitive-name policy;
- quoted assignment values are consumed completely through the closing quote
  (`OPENAI_API_KEY = "q7x9opaque rest8v"` keeps neither the tail nor the
  quotes); an unterminated quote fails closed as a whole item;
- once a sensitive Header is confirmed, its entire value is redacted
  (`Authorization: q7x9opaque;rest8v` never keeps the tail after the
  semicolon);
- cross-element CLI argument pairs in sequences: a sensitive flag (`--api-key`,
  `--token`, `--password`, ...) redacts the following array element as one
  whole item (`["--api-key", "SECRET"]`) **even when that element starts with
  `-` or `--`** (`["--api-key", "--q7x9opaque"]`, `["--token", "-opaque"]`,
  `["--password", "--"]`), while non-sensitive arguments are preserved; a
  following element that deterministically belongs to another allowlisted flag
  (`--profile`, `--service`, `--port`, `--tail`, `--help`, `-h` or another
  sensitive flag) is never swallowed — the flag fails closed on its own;
- provider-key shapes are covered through the value of a sensitive name, never
  through guessing secret prefixes.

All parsing is linear and bounded (string capped at 2048 characters, at most
512 lines, names/values length-capped, horizontal whitespace bounded at 256
with over-limit whole-item fail-closed). Sensitive item values exceeding the
single-item parse limit **fail closed as a whole item**: the entire item
becomes `[REDACTED]` — never a truncated 512-char prefix that leaks the tail.
A keyword-marker check remains as a deterministic fail-closed fallback.
`LifecycleResult` stdout/stderr, status/health/log text, exception text and
serialized diagnostics all pass through this protection.

The attack matrix (including opaque secrets with no token/secret/password
keyword) is in `backend/tests/test_runtime_redaction_attacks.py`; focused
lifecycle wrapper tests (exact argument arrays with explicit
`--env-file .env.example`, no shell, allowlists, Hardened rejection, timeout
and executable-not-found behavior, bounded/redacted output, bind-failure
propagation, `logs --tail` bounds, status/health failure behavior, Windows
paths without command injection, root `.env` never selected, the four
container-engine resolution cases on the probe and lifecycle sides, and the
controlled Podman Compose path) are in
`backend/tests/test_runtime_lifecycle.py`.

## Capability schema and provenance

Every reported fact carries a source/provenance and an evidence state:

| Field | Provenance | Evidence states |
|---|---|---|
| os, architecture | `platform` module | detected / unknown |
| memory_bytes | `os.sysconf` probe | detected / unknown |
| disk_free_bytes | `shutil.disk_usage` probe | detected / unknown |
| gpu | bounded `nvidia-smi` probe or Apple Silicon platform probe | available / detected / unavailable / unknown / not_applicable |
| container_engine | shared `resolve_container_engine` bounded `docker compose version` / `podman compose version` probes (docker then podman; only exit 0 resolves) | available / not_proven (executable presence is not a compose provider) |
| docker/podman executable | `shutil.which` | detected / unknown |
| docker/podman compose provider | bounded `shell=False` `compose version` probe | available (exit 0) / not_proven |
| local_mode_available | derived from a verified compose provider only | available / not_proven |
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
