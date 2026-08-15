# P6.4 Personal Agent Practice

Status: **implementation in progress; no production acceptance claim until the
real DeepSeek receipt passes from a clean production-mode target**.

P6.4 is the personal edition's transition from a capable workbench to bounded
Agent practice. It proves exactly three outcomes: uploaded-file RAG with
measured citation accuracy, small useful artifact creation, and an audited,
reversible modification of a disposable Workspace. Each outcome must run once
with one Agent and once with a real 3-6 Agent roster.

This is not the enterprise Planner or autonomous Multi-Agent Runtime. One human
Owner starts one request. The roster is fixed before the first Provider call;
specialists cannot wake peers, spawn descendants, change the graph or retry an
unknown result. The current personal canary retains one active invocation, so
participants run in a deterministic sequence. Every participant is a distinct
Model Gateway call with its own requested/actual model identity and usage; the
last participant is the parent synthesizer. A prompt that merely asks one model
to imitate several roles is not P6.4 evidence.

## Closed practice contract

Allowed participant counts are `1`, `3`, `4`, `5` and `6`. Team counts include
the parent. `2`, `7`, duplicate roles and unknown roles fail before a Provider
call. One member failure, cancellation, timeout, identity mismatch or unknown
outcome stops the remaining graph. The coordinator never automatically replays
an ambiguous call.

The implementation lives in `backend/src/omnibase/agent_practice/`:

- `contracts.py` defines the closed scenarios, participant roster and budgets;
- `service.py` performs separately metered sequential calls and parent synthesis;
- `scoring.py` grades exact fact-to-chunk citations without self-grading by an LLM;
- `artifacts.py` renders safe bytes from a bounded JSON specification;
- `changesets.py` applies and rolls back one UTF-8 replacement under exact CAS;
- `posture.py` rejects every environment except the narrow production personal window.

All model output is untrusted. The model can return an answer, artifact
specification or ChangeSet proposal; it cannot directly execute a script or
write a file. The local controller parses a closed JSON object, validates all
paths, sizes and digests, and performs the authorized operation. No shell, MCP,
arbitrary HTTP, arbitrary file API, SQL or recursive delegation is introduced.

## Scenario 1: upload, RAG and citation accuracy

The authoritative journey must start at the Browser upload endpoint. It must
not insert a `WorkspaceDerivedIndex` fixture and call that an upload test.
P6.3 currently has two disconnected data lanes: `/documents` indexes tenant
canonical chunks, while Personal Agent Alpha reads only ready
Workspace-derived chunks. P6.4 therefore requires a real, immutable
document-to-Workspace binding and a ready projection before the Agent may see
the content. Legacy tenant-only documents do not silently become visible in a
Workspace.

The acceptance corpus contains two unique facts in separate main documents and
two corresponding conflicting decoy documents in another Workspace. The
deterministic scorer checks fact precision, fact recall, citation precision,
citation recall, missing facts, duplicate claims, unsupported claims, unknown
chunk IDs, wrong-document chunks and whether the answer actually displays each
referenced `[n]` label. The fixed acceptance fixture requires all four
precision/recall values to equal `1.0` and every error count to equal zero. A
citations SSE event without correct use in the answer is not a pass.

Workspace-private Browser document list/get/download/delete operations require
the exact active membership recorded by the Resource binding. A missing or
drifted binding fails closed; legacy tenant-only documents keep their existing
tenant scope. The object upload precedes the initial metadata transaction, so
any failure before Document/Resource/Binding commit must remove the object; a
failed compensation is itself a veto. Normal ingestion writes canonical v1
Embeddings, which are authoritative. If a v2 shadow exists for the same source
document, retrieval suppresses that duplicate rather than presenting two
citation identities for one source.

## Scenario 2: small artifacts

The first artifact types are intentionally closed. A clock is rendered as one
dependency-free HTML file from safe title/theme fields. A slide deck is
rendered as offline HTML from bounded headings and bullets and is described as
HTML, never as PPTX. The trusted renderer escapes text, embeds no remote URL,
accepts no arbitrary markup or template path, and records media type, byte
length and SHA-256. A later real PPTX renderer requires a separate OPC/ZIP,
relationship, macro and external-link contract.

The production journey must prove the file exists in a run-scoped authorized
scratch Workspace, its digest matches the receipt, the clock DOM loads and its
time changes between samples. A model statement that it created a file is not
evidence.

## Scenario 3: Workspace modification

The first live target is a small disposable fixture, never the OmniBase source
tree. The parent returns a complete UTF-8 replacement for an allowlisted
existing file plus the exact before SHA-256. The Owner/acceptance controller
re-resolves the authorized root, rejects absolute paths, traversal, backslash
aliases, `.git`, `.env`, dependency/cache directories, links and non-files,
then performs compare-and-swap. It reads back the written bytes and verifies
the after digest. The deterministic project test runs outside the model.

Rollback is part of acceptance, not a later promise. It is permitted only if
the live file still has the exact applied digest; any user or external drift
becomes a conflict. Successful rollback restores the original tree digest.
Every lexical path component is checked with `lstat`; symlinks, Windows
junctions/reparse points and special files are rejected without traversal.
Apply and rollback write a complete same-directory temporary file, flush and
`fsync`, recheck the live digest, then use atomic replace. Temporary files are
removed on every outcome.

## Production window

Repository defaults and the final state remain false. During the exact,
time-limited single-Owner canary only:

```text
ENV=production
P6_4_PERSONAL_PRACTICE_ENABLED=true
AGENT_RUNTIME_ENABLED=true
AGENT_PLANNER_ENABLED=false
MULTI_AGENT_ENABLED=false
MCP_RUNTIME_ENABLED=false
PERSONAL_RUNTIME_PROFILE=personal_single_owner
max_concurrent_invocations=1
```

The narrow P6.4 gate does not replace the Runtime gate and cannot activate the
enterprise Planner/Multi-Agent system. Provider access remains inside Core's
Model Gateway. The production posture may assemble from the request-scoped
personal Provider resolver without a global operator `LLM_API_KEY`; it proves
only that the encrypted credential path and endpoint allowlist are configured.
The exact user credential is selected, decrypted and revalidated only inside
the invocation scope, and a missing, untested, revoked or drifted credential
still fails closed. Workload network destinations stay empty because these are
no-tool calls.

The real DeepSeek credential is accepted only through the encrypted personal
Provider credential path or an outside-repository, run-scoped secret. It must
not appear in the root `.env`, command arguments, Git, logs, screenshots,
Provider raw-response archives or evidence. Requested and actual model IDs must
match exactly. The current official aliases are `deepseek-v4-flash` and
`deepseek-v4-pro`; `/models` must prove availability before paid calls. Cache
hits are useful telemetry but best-effort and never a correctness gate.

## Required live matrix and receipt

The minimal live matrix is six product journeys:

1. one-Agent uploaded-file RAG;
2. three-Agent uploaded-file RAG;
3. one-Agent clock artifact;
4. four-Agent clock or HTML-slide artifact;
5. one-Agent disposable Workspace modification and rollback;
6. six-Agent disposable Workspace modification, validation and rollback.

The receipt schema is `omnibase.p6-4.personal-agent-practice.v1`. It may contain
source/image digests, logical run/task identities, ordered roles, requested and
actual model IDs, token counts, latencies, document/index/chunk IDs, numerical
scores, artifact/ChangeSet digests, rollback result, cleanup inventory and
before/during/after feature-gate states. It must reject keys, Authorization,
JWTs, prompts, full answers, document text, user file content, physical paths
and raw Provider errors.

P6.4 passes only after a clean production-mode target completes the full live
matrix, restores all Runtime/Planner/Multi-Agent/MCP gates to false, removes the
disposable data and verifies the redacted receipt. Offline unit tests and fake
Provider CI prove the contract but cannot substitute for that live result.

## Acceptance implementation

The live Browser matrix is implemented by
`scripts/production/run_p6_4_personal_agent_practice.py`. It accepts only an
explicit loopback target and logical Workspace/Agent identifiers. The Browser
access token and DeepSeek key come from the fixed process environment names
`OMNIBASE_P64_ACCESS_TOKEN` and `OMNIBASE_P64_DEEPSEEK_API_KEY`; neither secret
has a CLI option. The runner performs the Provider `/models` preflight, creates
and activates one encrypted personal credential, uploads the main and decoy
corpora through the product endpoint, executes the six journeys, revokes the
credential and removes the documents. Its output schema is
`omnibase.p6-4.personal-agent-practice-matrix.v1` and it always forces
`production_accepted=false` because target activation and closure are outside a
matrix fragment.

The complete disposable target is controlled by
`scripts/production/run_p6_4_personal_agent_practice_gate.py` over:

- `deployment/personal-production/compose.yml` for the closed production base;
- `deployment/personal-production/p6-4-acceptance.compose.yml` for the bounded
  practice gate, one ingestion worker and a run-scoped prewarmed embedding
  cache;
- `deployment/production/personal-runtime-canary.compose.example.yml` for the
  exact single-Owner Runtime canary.

The controller proves all gates closed before activation, creates two new
sentinel Workspaces and one sealed no-tool Agent, activates the exact canary,
runs the matrix, kills the canary, recreates the backend from the closed base,
proves every gate false, removes the exact labeled Compose project and volumes,
cleans the run-scoped local cache and secret env file, then calls
`validate_personal_practice_receipt()`. Only that final validator can accept the
`omnibase.p6-4.personal-agent-practice.v1` receipt.

Both the matrix and controller bind evidence to a clean source checkout. The
controller captures the clean HEAD before creating run material; the matrix
must report that exact HEAD; receipt creation rechecks that HEAD and the clean
worktree. Before run material is created, the controller performs read-only
`docker version` and `docker info` probes and requires an already-running Linux
Engine. It never starts or repairs Docker Desktop, WSL, Hyper-V or a virtual
disk. Within every roster, meta/citations/usage ordering is exact and every
invocation/task identity is unique. The final receipt additionally requires
identity uniqueness across all six journeys and exact fixed acceptance rosters.

The controller must not start or repair Docker Desktop, WSL, Hyper-V or a
virtual disk. If the Docker daemon is not already healthy, live acceptance
stops as not proved while static, unit, Compose-parse and receipt-contract work
continues. As of the current engineering worktree, the daemon was not running,
so the real DeepSeek six-journey receipt has not yet been produced and the
status remains implementation in progress.
