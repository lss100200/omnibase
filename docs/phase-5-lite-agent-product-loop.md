# Phase 5.4C Lite Agent Product Loop Contract

> **Status**: engineering-only, non-production.
> Production Runtime, Planner, multi-Agent, arbitrary tools and migration
> `0013` remain frozen. The three Phase 5 production Feature Gates
> (`AGENT_RUNTIME_ENABLED`, `AGENT_PLANNER_ENABLED`, `MULTI_AGENT_ENABLED`)
> must remain exactly `false`.

## Scope

P5.4C is the **engineering-only product surface** for the single-Agent loop.
It is the user-facing entry point that wraps the existing tool-free Agent Alpha
product loop (P5.2C). P5.4C is intentionally narrow:

- It adds one independent product gate (`AGENT_LITE_ENGINEERING_ENABLED`).
- It supports exactly one invocation mode: `no_tool`.
- It discloses the honest builder chain to the Browser status endpoint and the
  Next.js workbench, and explicitly labels the formal P5.4B composition as
  `not_integrated` in this product loop.
- It does **not** create a new migration, a new capability, a new tool, a new
  Planner, a new multi-Agent path, a new production Runtime, or a new
  database-backed authority, and it does **not** integrate the formal P5.4B
  builder (`build_engineering_single_agent_executor`) — that composition is a
  separate engineering seam assembled only by the P5.4B disposable PostgreSQL
  Gate with real persisted authority.

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

`AGENT_LITE_ENGINEERING_ENABLED` is a closed-set gate:

- `resolve_lite_agent_flag(raw)` is the **pure parser**: it takes an explicit
  input and never reads `os.environ`. `None`/`""`/`"false"` resolve to `False`
  (absent → default-off), `"true"` resolves to `True`, and any other token
  raises `LiteAgentConfigurationError` (fail closed).
- `runtime_lite_agent_enabled()` is the **runtime resolver**: the only place
  the gate reads the process environment (`os.environ.get(...)`), which it
  passes into the pure parser. The Browser dependency
  (`router.get_agent_alpha`) and the live posture use it, so setting
  `AGENT_LITE_ENGINEERING_ENABLED=true` genuinely enables the route.

| Input (pure parser)            | Result                        |
|--------------------------------|-------------------------------|
| `None`, `""`, `"false"`        | `False` (default-off)         |
| `"true"`                       | `True`                        |
| any other token                | `LiteAgentConfigurationError` |

The gate is a **product entry guard**, never an authorization fact. Enabling it
only authorizes the Lite Browser surface in a development/engineering
deployment.

## Invocation mode and builder chain

P5.4C supports exactly one invocation mode:

1. `no_tool` — the tool-free RAG-retrieval product loop carried by the P5.2C
   Agent Alpha seam (`build_engineering_agent_alpha`). `AlphaInvokeRequest` has
   no mode field because there is only one mode; the `/invoke` route always
   dispatches through the Alpha seam.

The formal P5.4B builder `build_engineering_single_agent_executor` (which
installs `LiveRuntimeAuthorityValidator` and
`CapabilityGatewayKnowledgeSearchPort`) is disclosed by name in the posture and
status DTO, but `formal_builder_integration` is always `not_integrated`: it is
not selectable, not routed and never assembled by this product loop. A builder
name in a status DTO is never a supported mode.

## Status DTO

`AlphaStatusResponse` exposes:

- `lite_gate_enabled`, `engineering_assembled`, `engineering_flag_enabled`,
  `environment_allowed`, `phase5_gates_all_false`,
  `production_activation_allowed` (always `false`), `tools_enabled` (always
  `false`), `multi_agent_enabled` (always `false`).
- `formal_builder` (disclosure name only), `alpha_builder`,
  `supported_invocation_modes` (always `["no_tool"]`),
  `formal_builder_integration` (always `"not_integrated"`),
  `expected_migration_head` (`"0012"`).

Provider secrets, physical locators, credentials, migration internals and
runtime handles must never appear in the DTO.

## Disposable Gate

`scripts/production/run_p5_4c_lite_agent_product_disposable_gate.py` is a
run-scoped, engineering-only disposable Gate with three modes:

- `--validate-only`: parses the offline contract without Git hashing or command
  execution and never returns `ready`.
- `--run`: requires a clean checkout, executes the focused Lite unit suite and
  a live gate probe inside the backend container, and seals the source
  manifest, command receipts and measurements under unique raw-byte SHA-256
  sidecars.
- `--verify-evidence <path>`: independently re-verifies the sealed source,
  artifact and evidence bytes, re-parses the probe receipt and re-derives every
  claim from the recorded command vectors.

Every claim in the report is **derived from an executed receipt or a sealed
file measurement** — nothing is hardcoded as a measurement:

- parser/resolver/posture claims are parsed from the sealed probe stdout;
- `migration_head` is discovered from the migration directory and the typed
  executor example config on every run and every verification;
- `root_env_accessed`, `business_database_accessed` and
  `business_database_migrated` are re-derived from the recorded command
  vectors;
- `formal_builder_integration` is reported `not_proven`: this Gate never
  executes the formal P5.4B persisted composition (that belongs to the P5.4B
  disposable PostgreSQL Gate), so it never claims integration.

The run directory is **preserved** on success and on failure and can be
re-verified later; the Gate never deletes its own evidence. The Gate never
reads the root `.env`, never touches a business database, never creates
migration `0013`, and never opens a Phase 5 production Feature Gate.

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
outcome, present a disposable Lite Gate as production admission, present the
formal P5.4B builder as an integrated mode, or expose provider secrets in
browser state, logs, diagnostics, errors or DTOs.
