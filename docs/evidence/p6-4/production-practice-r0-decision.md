# P6.4 Personal Agent Practice — production practice R0 decision

Status: **P6_4_PERSONAL_PRODUCTION_PRACTICE_ACCEPTED**

Decision date: 2026-08-16 (Asia/Shanghai)

This decision records the successful bounded personal-edition production-mode
practice Gate. It supersedes only the live-evidence blocker recorded in
`engineering-r0-decision.md`. It does not approve the P34.7 Trust Policy,
activate the enterprise Planner or Multi-Agent Runtime, deploy OmniBase, or
authorize a business database migration.

## Accepted source and receipt

The outer disposable-target controller started from and returned to a clean,
unchanged executable source checkout:

```text
source HEAD = 3c3d322e3f9871749da00525eaac9505062026b4
branch = codex/p6-4-agent-practice-r0
receipt schema = omnibase.p6-4.personal-agent-practice.v1
receipt SHA-256 = 5e8145525e75feb84d6a28d3cf1007e078747f8e73ac7bd62b8b462b5978f0ef
production_accepted = true
provider = deepseek
model = deepseek-v4-flash
models preflight = passed
```

The redacted receipt remains operator-retained outside Git. The repository
records its digest and independently checked facts, not the Provider key,
Browser token, prompts, answers, source documents, physical paths or raw
Provider responses. Documentation-only forward commits after the Gate do not
change the accepted executable source SHA and do not claim a second paid run.

## Six accepted product journeys

The Gate completed the exact required matrix. Every participant was a separate
identity-checked and metered Model Gateway call. The rosters include the final
parent and ran serially under the single-owner canary:

| Journey | Scenario | Roster | Provider calls |
| --- | --- | --- | ---: |
| `rag_single` | Browser upload, Workspace RAG and citation scoring | `parent` | 1 |
| `rag_three` | Browser upload, Workspace RAG and citation scoring | `data`, `qa`, `parent` | 3 |
| `artifact_single` | Dependency-free clock HTML | `parent` | 1 |
| `artifact_four` | Offline HTML slide deck | `product`, `ux`, `frontend`, `parent` | 4 |
| `workspace_single` | Disposable Workspace CAS write, check and rollback | `parent` | 1 |
| `workspace_six` | Disposable Workspace CAS write, check and rollback | `product`, `frontend`, `backend`, `security`, `qa`, `parent` | 6 |

The total is exactly 16 Provider calls, 16 unique invocation identities and 16
unique task identities. No prompt simulated multiple roles, no specialist
self-woke or delegated, and no ambiguous outcome was automatically retried.

## RAG acceptance

Both the one-Agent and three-Agent journeys traversed the Browser upload path,
proved the immutable Workspace binding, waited for the authoritative ready
index and excluded the decoy Workspace. Trusted local scoring, rather than the
model's self-assessment, produced:

```text
fact precision = 1.0
fact recall = 1.0
citation precision = 1.0
citation recall = 1.0
expected facts = 2
supported claims = 2
unsupported claims = 0
missing facts = 0
wrong chunks = 0
unknown chunks = 0
statement mismatches = 0
```

These values were independently rechecked for both RAG journeys. The receipt
does not retain the acceptance facts, answers or source text.

## Artifact acceptance

The one-Agent journey produced `clock.html` as the closed `clock_html` type.
The trusted renderer verified the bytes and digest, loaded the DOM, proved the
clock value changed between samples and confirmed that the document has no
online dependency.

The four-Agent journey produced `slides.html` as the closed `slides_html`
type. Its bytes and digest were verified, the DOM loaded and all dependencies
remained offline. This is intentionally an HTML slide artifact and is not
misrepresented as PPTX.

## Workspace acceptance

Both Workspace journeys targeted only the run-scoped disposable fixture and
the logical path `src/acceptance.txt`. Trusted code proved the disposable root,
applied the proposal under exact before-CAS, verified the written bytes, ran
the deterministic project check, rolled the change back under after-CAS and
restored the original tree digest:

```text
before file digest != applied file digest
before tree digest != applied tree digest
before tree digest == rollback tree digest
cas_applied = true
post_write_verified = true
project_check_passed = true
rollback_verified = true
original_tree_restored = true
```

The OmniBase source tree and user Workspaces were not mutation targets.

## Production posture and cleanup

Before and after the canary, Runtime, personal practice, Planner, enterprise
Multi-Agent and MCP were all false. During the exact window the target used:

```text
ENV=production
PERSONAL_RUNTIME_PROFILE=personal_single_owner
max_concurrent_invocations=1
AGENT_RUNTIME_ENABLED=true
P6_4_PERSONAL_PRACTICE_ENABLED=true
AGENT_PLANNER_ENABLED=false
MULTI_AGENT_ENABLED=false
MCP_RUNTIME_ENABLED=false
```

The final receipt proves:

```text
all_feature_gates_closed = true
disposable_documents_removed = true
disposable_workspaces_removed = true
provider_credential_revoked = true
runtime_canary_closed = true
```

An independent host audit after the Gate found zero P6.4 containers, networks
and volumes. The run-scoped evidence root retained only the redacted receipt.
The fixed secret environment names were absent from the auditing process and
the clipboard was empty.

## Redaction audit

The complete receipt bytes were scanned without printing their contents. Match
counts were zero for Provider-key shapes, Bearer tokens, JWTs, HTTP(S) URLs,
Windows absolute paths, the four private acceptance facts and fields named as
prompt, answer, source text or raw response. The receipt contains only the
allowed logical identities, role/model metadata, counts, metrics and digests.

## Engineering findings closed during the real Gate

The live run exposed issues that unit and fake-Provider tests did not fully
exercise. They were fixed with forward-only commits and reverified before the
accepted run:

- stable, redacted Agent Alpha, Task Ledger and Gateway failure codes now reach
  the P6.4 coordinator without physical paths, SQL text, endpoint details or
  Provider payloads;
- every synchronous SSE generator step re-enters and exits the exact tenant
  scope inside one `next()` operation, avoiding cross-worker context-token
  misuse while preserving tenant database isolation;
- bounded `websearch_to_tsquery` retrieval uses a small safe OR query rather
  than AND-ing the entire composed multi-Agent prompt, restoring exact RAG
  recall without accepting raw query syntax;
- specialists use bounded economy responses and DeepSeek thinking is disabled
  for that gear, while the final parent retains standard reasoning;
- visible output is reserved for the parent result and specialist JSON is
  length- and shape-bounded;
- an exactly wrapped single Markdown JSON fence is canonicalized, while extra
  prose, multiple objects, arrays and malformed responses still fail closed.

The accepted executable source is the end of this review-fix chain. No code
change was made after its receipt was produced.

## Independent audit conclusion

The first audit draft expected 18 Provider calls. The fixed matrix arithmetic
is `1 + 3 + 1 + 4 + 1 + 6 = 16`; after correcting that auditor-only constant,
the complete independent audit passed with no remaining finding. The product
receipt itself was not changed or resealed to hide a failure.

## Authority boundary

This result means:

```text
P6_4_PERSONAL_PRODUCTION_PRACTICE_ACCEPTED
REAL_DEEPSEEK_SINGLE_AND_3_TO_6_AGENT_MATRIX_PASSED
UPLOAD_RAG_CITATION_ACCEPTANCE_PASSED
OFFLINE_ARTIFACT_ACCEPTANCE_PASSED
DISPOSABLE_WORKSPACE_CAS_AND_ROLLBACK_ACCEPTANCE_PASSED
FINAL_PERSONAL_CANARY_CLOSED
```

It does not mean:

```text
P34_7_TRUST_POLICY_APPROVED
ENTERPRISE_P5_TOTAL_PRODUCTION_AUTHORIZED
PLANNER_OR_ENTERPRISE_MULTI_AGENT_ACTIVATED
MCP_TO_AGENT_ACTIVATED
BUSINESS_DATABASE_MIGRATED
DEPLOYED_OR_RELEASED
```

P34.7 and the enterprise P5 total-production authorization remain separate
tracks. Migration head remains `0016`, migration `0017` is absent, default
Runtime/Planner/Multi-Agent/MCP gates remain false, and this branch has not
been pushed, merged or deployed by this acceptance run.
