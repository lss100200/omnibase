# P5.4C Lite Agent Product Loop — Quick Start & Demo

> **Engineering-only.** This walkthrough describes the disposable
> engineering-only Lite Agent product surface. It is **not** a production
> capability: production Runtime, Planner, multi-Agent, arbitrary tools and
> migration `0013` remain frozen, and the three Phase 5 production Feature
> Gates must stay exactly `false`.

## What you get

- One installed AgentVersion per Workspace.
- A tool-free RAG-retrieval product loop (P5.2C Alpha seam).
- The formal P5.4B knowledge-search composition path
  (`build_engineering_single_agent_executor`) for the read-only
  `knowledge_search` capability — gated behind the formal builder flag.
- A durable Task/Run/Attempt/Effect ledger and SSE invocation stream.
- Honest status: the UI labels the live builder chain, the supported invocation
  modes and the gate posture; it never fabricates success with frontend-only
  mock data.

## Prerequisites

- Docker with the `omnibase-backend:latest` and
  `pgvector/pgvector:0.8.5-pg15-bookworm` images present.
- Repository checkout on the `external/p5-4c-lite-agent-product-loop` branch.
- `.env.example` only — never read or stage the repository root `.env`.

## Opening the Lite gate (engineering only)

The Lite gate is independent of the three production Phase 5 Feature Gates and
defaults off. Set exactly `true` or `false` in your local engineering
environment; any other token fails closed.

```text
AGENT_LITE_ENGINEERING_ENABLED=true
AGENT_ALPHA_ENGINEERING_ENABLED=true        # tool-free Alpha loop (P5.2C)
P5_4B_ENGINEERING_ENABLED=true              # formal knowledge-search builder (P5.4B)
AGENT_RUNTIME_ENABLED=false
AGENT_PLANNER_ENABLED=false
MULTI_AGENT_ENABLED=false
ENV=development
```

All three Phase 5 production Feature Gates must remain `false`. Migration head
must remain `0012` (migration `0013` is not created).

## Exact UI steps

1. Sign in and open **/agents** (AI Employee Workbench).
2. The header badge reads `LITE GATE ON` when the gate is open and `LITE GATE
   OFF` when it is closed; `TOOLS DISABLED` and `PRODUCTION RUNTIME OFF` are
   always shown.
3. In **Invocation target**, select an existing Workspace. If you have none,
   the panel shows an explicit empty state telling you to create one via the
   Workspace governance API (this surface never bypasses membership/scope).
4. Select a sealed, installed AgentVersion. If none is installed, use
   **New employee** to seal and install one, or ask your operator.
5. The **Workspace surfaces** panel labels each surface honestly:
   `LIVE`/`SELECT` for Workspace/AgentVersion selection, `GATED`/`LOCKED` for
   read-only knowledge search, and `ROADMAP`/`LOCKED` for Projects/Skills/MCP/
   Marketplace (not backed by current product state).
6. The **Runtime posture** panel discloses the formal knowledge-search builder
   (`build_engineering_single_agent_executor`, flag on/off, knowledge_search
   gated/locked) and the tool-free loop builder
   (`build_engineering_agent_alpha`).
7. Type a prompt and press Enter or the send button. The invoke button is
   disabled until the Lite gate is open and a Workspace + AgentVersion are
   selected.
8. The agent streams `meta → citations → chunk* → usage → done`. If the
   environment is not assembled (gate closed, wrong environment, provider
   unavailable, migration head not 0012) the stream returns a stable
   `agent_alpha_unavailable` reason code; production Runtime never activates.

## Negative states

- **Gate closed**: the empty state reads "Lite product gate is closed" and the
  send button stays disabled.
- **No Workspace**: the empty state reads "Select a Workspace to begin".
- **No installed AgentVersion**: the empty state reads "No sealed AgentVersion
  installed".
- **Alpha not assembled**: the Runtime posture reads the honest reason (check
  provider, environment, Phase 5 gates, migration head 0012).
- **Provider unavailable**: the SSE stream surfaces a stable
  `agent_alpha_provider_unavailable` / `agent_alpha_unavailable` reason code.

## Verification commands

```text
python scripts/production/run_p5_4c_lite_agent_product_disposable_gate.py --validate-only
docker compose --env-file .env.example run --rm --no-deps backend pytest tests/test_p5_4c_lite_gate.py tests/test_agent_alpha_engineering.py -q
docker compose --env-file .env.example run --rm --no-deps -v .:/workspace -w /workspace/backend backend pytest tests/test_p5_4c_lite_agent_product_gate.py -q
cd frontend && pnpm typecheck && pnpm lint && pnpm test && NODE_ENV=production pnpm build
```

## Production boundary

Production status remains `blocked/not_proven`. The disposable Lite Gate is
engineering evidence only; it is not production admission and does not open any
Phase 5 production Feature Gate, migration `0013`, or a production Runtime.
