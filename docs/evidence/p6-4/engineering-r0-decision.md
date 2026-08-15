# P6.4 Personal Agent Practice — engineering R0 decision

Status: **IMPLEMENTATION_STARTED_LIVE_PRODUCTION_ACCEPTANCE_NOT_YET_PROVEN**

This record covers the engineering implementation on top of P6.3. It is not
the final P6.4 receipt and does not claim that the clean production target has
completed the real DeepSeek six-journey matrix.

Implemented and locally verified:

- closed participant counts `1` and `3-6`;
- deterministic request-scoped rosters with one real Gateway call per member
  in the test contract and the parent as final synthesizer;
- exact requested/actual model projection and bounded token/call accounting;
- deterministic fact/chunk citation precision and recall scoring;
- trusted offline clock and HTML-slide renderers with byte digests;
- disposable-root UTF-8 before-CAS, post-write verification and exact rollback;
- strict production posture requiring Runtime true while Planner, enterprise
  Multi-Agent and MCP remain false;
- attack tests for invalid team sizes, wrong citations, unsafe paths, drift and
  enterprise Multi-Agent activation.
- Browser `POST /documents` upload to immutable Workspace resource binding,
  worker activation of the authoritative Agent-readable Embedding lane and
  compensating object cleanup on membership races or initial metadata commit
  failure; Workspace-private Browser reads/deletes now require the exact active
  membership and canonical v1 chunks suppress a same-source v2 shadow;
- durable Browser practice API and UI with per-node invocation/task/model/usage
  projection, exact metadata ordering, cross-node identity uniqueness,
  deterministic progress, current-node cancellation and proposal-only outputs;
- strict redacted final receipt validator covering the exact six journeys,
  before/during/after gate posture and cleanup proof;
- loopback-only live matrix runner with secret-free CLI, direct DeepSeek
  `/models` preflight, encrypted personal Provider lifecycle and mandatory
  document/credential cleanup;
- disposable production target controller that alone may set
  `production_accepted=true` after canary closure, all gates false, zero labeled
  Compose resources, clean unchanged source binding and strict receipt
  validation; an already-running healthy Linux Docker Engine is required by a
  read-only preflight before any run material is created;
- production Compose overlay with a run-scoped embedding prewarm, one bounded
  ingestion worker and no Planner, enterprise Multi-Agent or MCP Runtime.

Still required before P6.4 can pass:

- clean production-mode real DeepSeek runs for all six acceptance journeys;
- redacted receipt verification, cleanup proof and final gate rollback.

Latest independent local verification after the security forward-fixes:

```text
P6.4 focused backend + runner/controller = 100 passed
Document/Worker/RAG/rate-limit related regression = 126 passed
Model Gateway/personal Agent/per-role model regression = 81 passed
Frontend unit tests = 196 passed
Frontend typecheck + lint + production build = passed
Frontend changed-path Prettier = passed
Full frontend Prettier baseline = not clean (95 pre-existing files outside this
  P6.4 changed-path claim); no broad formatting rewrite was performed
Ruff changed-path check + format --check = passed
Targeted Mypy = 17 source files, no issues
Maintainer map = valid (71 invariants / 50 modules / 345 entrypoints)
Maintainer benchmark = valid (3 plans / 8 scenarios)
P5.1A/P5.2A/P5.3A sealed-contract regression = 407 passed after raw-byte
  reseal (Registry -> Task Ledger -> Planner)
Broad Windows-host non-integration (Linux launcher module excluded) =
  2863 passed / 42 skipped / 16 deselected / 13 failed
The 13 failures are outside P6.4: 11 require POSIX/Linux primitives
  (`geteuid`, `killpg`, Linux absolute paths, cgroup/private-mode semantics)
  and 2 are host dependency drift (FastAPI 0.141.1 / Starlette 1.3.1 versus
  locked FastAPI 0.116.2 / Starlette 0.48.0 route introspection behavior)
three-file Compose merge config --quiet = passed with explicit non-secret
  placeholder coordinates and no root .env access
```

The controller now performs its own read-only Docker Linux Engine health
preflight after proving a clean source HEAD and before creating any disposable
files. It cannot start or repair Docker/WSL. A current read-only host probe
again found the `dockerDesktopLinuxEngine` pipe absent. The real Gate therefore
cannot run on this host at present; the DeepSeek matrix and final accepted
receipt remain unexecuted.

Secrets were not read from the root `.env`, written to this repository or
included in this evidence. No Docker/WSL/VHDX mutation, business database
access, deployment, push, PR or merge was performed by this implementation
slice; the Docker calls were limited to Compose parsing and a read-only daemon
availability probe.

```text
P6_4_ENGINEERING_IMPLEMENTATION_ADVANCED_SECURITY_FORWARD_FIX_APPLIED
P6_4_FOCUSED_100_PASSED
LIVE_MATRIX_RUNNER_IMPLEMENTED_AND_OFFLINE_VERIFIED
FINAL_ACCEPTANCE_CONTROLLER_IMPLEMENTED_AND_OFFLINE_VERIFIED
DOCKER_LINUX_ENGINE_PIPE_ABSENT_READ_ONLY_PROBE
REAL_DEEPSEEK_SIX_JOURNEY_RECEIPT_NOT_EXECUTED
PRODUCTION_ACCEPTANCE_NOT_PROVEN
```
