# P6.0 Personal Engineering Workbench R0 Decision

Date: 2026-08-13

Decision: `P6_0_ENGINEERING_ACCEPTED_PENDING_OWNER_BROWSER_REVIEW`

## Delivered boundary

- P6.0-A: tenant/user-scoped browser sessions, timeline, one parent Agent and
  nine request-scoped dormant specialists with exactly one explicit `@` target.
- P6.0-B: direct Owner directory authorization, lazy bounded logical file tree,
  text/image/PDF preview, binary metadata view, and separate
  `OPEN / CONTEXT / PINNED` states.
- P6.0-C: successful Task/invocation-bound Owner-reviewed local text
  ChangeSets, Before/After audit, digest checks and bounded three-way rollback.
- P6.0-D: economy/standard/deep/audit gears, real `top_k` and local context
  budgets, observed DeepSeek/GLM/Kimi/GPT/Claude profiles, reasoning usage and
  rate-not-configured cost display.

## Security decision

The browser does not scan the computer or persist handles, file bodies or
absolute paths. Secret names and traversal fail closed. Selected files are
re-read before invocation and injected only as explicitly untrusted JSON data.
The exact final request remains bounded to 32,000 characters.

Agent Alpha still has no file tool. A ChangeSet records only an Owner-reviewed
local edit after a successful Task; it never converts natural-language output
into an implicit filesystem action. Writes use snapshot comparison and
post-write digest verification. Rollback uses three-way merge and a final CAS
read. Browser writes are not cross-file atomic, so interrupted results are
reported as `recovery_required` rather than success.

Provider identity arrives after dispatch, therefore the active request uses
generic adaptation. The UI updates only from SSE evidence. Native provider
reasoning, target output control, Tools, MCP, CLI, Vision and autonomous
delegation remain unavailable and visibly disabled. Cost remains unknown
without explicit rates.

## Local verification

```text
frontend unit/contract tests = 146 passed
frontend typecheck = passed
frontend lint = passed
changed-path Prettier = passed
git diff --check = passed
production build = passed (`/dashboard` 30.7 kB, 183 kB first load)
maintainer map = valid (57 invariants / 46 modules / 894 path specs / 308 entrypoints)
maintainer benchmark = valid (3 plans / 8 scenarios / 9 unsafe vetoes)
P5 registry/task-ledger/planner `--verify` = each exit 2, blocked/not_proven, contract_valid=true, clean=true, vetoes=[]
```

Docker/WSL was intentionally not started because the host virtual-disk incident
is governed by INV-064 and P6.0 changed only browser/frontend boundaries. The
root `.env` was not read. No database was accessed or migrated. Migration head
remains `0015`; migration `0016` is absent. Planner and Multi-Agent remain
disabled. Enterprise P34.7 remains frozen. P6.x was not started.

The maintenance map and INV-065 through INV-067 now cover P6.0-B/C/D. The
registry, task-ledger and planner example contracts were resealed in dependency
order against the final raw bytes. Their formal verifiers still require a clean
checkout and are run only after the explicit P6.0 commit; the expected posture
remains `blocked/not_proven` with no veto, not production activation.

## Not claimed

- not pushed, merged or deployed;
- no system-default application opening without a native bridge;
- no symlink-proof filesystem isolation claim;
- no Agent automatic local-file mutation;
- no multi-file transaction or enterprise atomic rollback;
- no native provider reasoning parameter or output-token control;
- no current vendor pricing claim;
- no Skills, MCP, SQL visualization, Email, CLI, browser/desktop control or
  autonomous self-modification.
