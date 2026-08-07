# Phase 5.4C Lite Agent Product Loop Contract

> **Status**: engineering-only, formal-builder-bound, non-production.
> Production Runtime, Planner, multi-Agent, arbitrary tools and migration
> `0013` remain frozen. The three Phase 5 production Feature Gates
> (`AGENT_RUNTIME_ENABLED`, `AGENT_PLANNER_ENABLED`, `MULTI_AGENT_ENABLED`)
> must remain exactly `false`.

## Scope

P5.4C is the **engineering-only product surface** for the single-Agent loop.
It is the user-facing entry point that wraps the existing tool-free Agent Alpha
product loop (P5.2C) and the formal P5.4B knowledge-search composition. P5.4C
is intentionally narrow:

- It adds one independent product gate (`AGENT_LITE_ENGINEERING_ENABLED`).
- It discloses the honest builder chain and the supported invocation modes to
  the Browser status endpoint and the Next.js workbench.
- It does **not** create a new migration, a new capability, a new tool, a new
  Planner, a new multi-Agent path, a new production Runtime, or a new
  database-backed authority.

## Authority sources

- `backend/src/omnibase/agent_alpha/lite.py`
- `backend/src/omnibase/agent_alpha/router.py`
- `backend/src/omnibase/agent_alpha/schemas.py`
- `backend/src/omnibase/agent_alpha/engineering.py`
- `backend/src/omnibase/agent_executor/engineering.py`
- `frontend/app/(dashboard)/agents/page.tsx`
- `frontend/lib/api.ts`
- `scripts/production/run_p5_4c_lite_agent_product_disposable_gate.py`

## The Lite gate

`AGENT_LITE_ENGINEERING_ENABLED` is a closed-set gate parsed by
`resolve_lite_agent_flag(raw)`:

| Input                         | Result                              |
|-------------------------------|-------------------------------------|
| `None`, `""`, `"false"`       | `False` (default-off)               |
| `"true"`                      | `True`                              |
| any other token               | `LiteAgentConfigurationError`       |

The parser is independent of the ambient host environment. `raw=None` is
documented to mean "the variable is absent" and resolves to `False` even when a
stray `AGENT_LITE_ENGINEERING_ENABLED` is set in the process environment.
`lite_agent_posture(raw=..., env=...)` accepts an explicit `env` mapping so the
posture is reproducible in tests; production callers pass the live process
environment.

The gate is a **product entry guard**, never an authorization fact. Enabling it
only authorizes the Lite Browser surface in a development/engineering
deployment.

## Builder chain

P5.4C supports exactly two invocation modes:

1. `no_tool` — the tool-free RAG-retrieval product loop carried by the P5.2C
   Agent Alpha seam (`build_engineering_agent_alpha`).
2. `knowledge_search_read_only` — the formal reviewed P5.4B composition builder
   `build_engineering_single_agent_executor`, which installs
   `LiveRuntimeAuthorityValidator` and `CapabilityGatewayKnowledgeSearchPort`
   and is the **only** knowledge-search-capable path.

The P5.2C Alpha seam must never be presented as the knowledge-search authority
path. `lite_agent_posture()` exposes the honest builder chain
(`formal_builder`, `alpha_builder`, `supported_invocation_modes`) so the UI can
label state without authorizing anything. The
`knowledge_search_read_only_enabled` posture field is `True` only when the Lite
gate, the formal P5.4B builder flag and all-false Phase 5 gates all hold.

## Status DTO

`AlphaStatusResponse` exposes:

- `lite_gate_enabled`, `engineering_assembled`, `engineering_flag_enabled`,
  `environment_allowed`, `phase5_gates_all_false`,
  `production_activation_allowed` (always `false`), `tools_enabled` (always
  `false`), `multi_agent_enabled` (always `false`).
- `knowledge_search_read_only_enabled`, `formal_builder`,
  `alpha_builder`, `supported_invocation_modes`,
  `formal_builder_flag_enabled`, `expected_migration_head` (`"0012"`).

Provider secrets, physical locators, credentials, migration internals and
runtime handles must never appear in the DTO.

## Disposable Gate

`scripts/production/run_p5_4c_lite_agent_product_disposable_gate.py` is a
run-scoped, engineering-only disposable Gate with three modes:

- `--validate-only`: parses the offline contract without Git hashing or command
  execution and never returns `ready`.
- `--run`: requires a clean checkout, exercises the Lite gate parser and the
  focused Lite unit suite inside the backend container, seals the source
  manifest, command receipts and measurements under unique raw-byte SHA-256
  sidecars, prints the report, then removes the run directory on success so the
  repository keeps zero disposable residues. A failed run preserves the
  directory for inspection.
- `--verify-evidence <path>`: independently verifies the sealed source,
  artifact and evidence bytes plus the command receipts.

The Gate never reads the root `.env`, never touches a business database, never
creates migration `0013`, never opens a Phase 5 production Feature Gate, and
does not replace the heavier P5.4B disposable PostgreSQL Gate (which remains
the authority for the formal composition with real persisted runtime/lease
facts).

## Verification

```text
python scripts/production/run_p5_4c_lite_agent_product_disposable_gate.py --validate-only
docker compose --env-file .env.example run --rm --no-deps backend pytest tests/test_p5_4c_lite_gate.py tests/test_agent_alpha_engineering.py -q
docker compose --env-file .env.example run --rm --no-deps -v .:/workspace -w /workspace/backend backend pytest tests/test_p5_4c_lite_agent_product_gate.py -q
cd frontend && pnpm typecheck && pnpm lint && pnpm test && NODE_ENV=production pnpm build
python scripts/maintenance/validate_maintainer_map.py --repo-root .
python scripts/maintenance/validate_maintainer_benchmark.py --repo-root .
```

## Production boundary

Production status remains `blocked/not_proven`. The Lite gate, the disposable
Gate, the focused unit suite and the frontend build are engineering evidence
only; they are not production admission. P5.4C must never enable a Phase 5
production Feature Gate, create migration `0013`, retry an unknown provider
outcome, present a disposable Lite Gate as production admission, or expose
provider secrets in browser state, logs, diagnostics, errors or DTOs.
