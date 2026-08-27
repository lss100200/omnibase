# P6.0-D2 Model Adaptation and Per-Role Selection

Status: **local implementation in progress; no push, merge or deployment**

P6.0-D2 gives the personal engineering workbench researched, conservative
prompt profiles for DeepSeek, GLM, Kimi, GPT and Claude, plus one model-setting
slot for the parent and each of nine dormant specialists. It remains one
personal Agent Alpha Runtime. It does not create ten autonomous Agents and does
not enable Planner, Multi-Agent, Skills, MCP, CLI, Vision or arbitrary tools.

## Family resolution

The order is:

```text
user-entered model name
> Provider-returned observed actual model
> explicit family override
> Provider/base-URL hint
> generic
```

NFKC and case normalization apply. Recognized model-name evidence is never
overridden by a relay URL. A name containing tokens from more than one family,
such as `claude-gpt-bridge`, resolves to generic. Classification selects only a
prompt profile. It is not proof of native reasoning state, structured output,
prompt cache, context length, image support or tool calling.

The researched profiles are dated 2026-08-13 and intentionally conservative:

- DeepSeek: thinking/tool continuation can require preserved reasoning content;
  strict tool schemas are not assumed for a compatible relay.
- GLM: text and visual model families stay distinct; thinking continuation is
  not enabled by name alone.
- Kimi: exact models differ in context, vision and strict-schema behavior;
  complete assistant-state continuation is not assumed for third-party URLs.
- GPT: Responses state, compaction, structured outputs and reasoning controls
  require an exact verified endpoint.
- Claude: thinking budget, structured output, cache breakpoints and vision are
  not projected onto an arbitrary OpenAI-compatible relay.

The public-document review used the vendors' own developer documentation, not
relay marketing pages:

- DeepSeek, [Thinking Mode](https://api-docs.deepseek.com/guides/thinking_mode/)
- Zhipu AI, [GLM-4.5](https://docs.bigmodel.cn/cn/guide/models/text/glm-4.5)
- Moonshot AI, [Kimi developer documentation](https://platform.kimi.com/docs)
- OpenAI, [Reasoning models](https://developers.openai.com/api/docs/guides/reasoning)
- Anthropic, [Extended thinking](https://platform.claude.com/docs/en/build-with-claude/extended-thinking)

The review establishes only that vendor-native endpoints expose different
continuation, reasoning, tool, context and multimodal contracts. It does not
establish that a user-supplied relay implements any of them. Consequently D2
uses those facts to choose bounded prompts and context handling only; native
request fields stay off until a later exact-endpoint capability handshake.

All native controls remain disabled in D2.

## Ten role settings

The closed role set is:

```text
parent, product, ux, frontend, backend,
data, security, qa, operations, docs
```

Every role defaults to the user's active default Provider credential, including
its URL, encrypted API key and model. A role may select another saved credential
and/or provide another model name. Returning to inheritance deletes only the
override row. The key is never copied into the role table or returned to the
browser.

The Browser API is:

```text
GET    /api/v1/workspaces/{workspace}/agents/{version}/model-settings
PUT    /api/v1/workspaces/{workspace}/agents/{version}/model-settings/{role}
DELETE /api/v1/workspaces/{workspace}/agents/{version}/model-settings/{role}
POST   /api/v1/workspaces/{workspace}/agents/{version}/model-settings/{role}/test
```

PUT requires `expected_version`, with `0` for first creation. DELETE requires
the exact current version. The UI refuses mutation until the settings projection
for the exact Workspace/AgentVersion has loaded.

## Exact model test and dispatch identity

A custom model name is pending until a no-tool probe returns an actual model ID
equal to the requested ID. The external call is bracketed by live-scope
revalidation. Its evidence digest binds:

- override ID and version;
- credential ID/version and key version/fingerprint;
- Provider ID and validated base URL;
- requested model ID and credential active/revoked state.
- Workspace generation, installed Binding ID and AgentVersion digest;
- resolved public endpoint address set and canonical allowlist policy digest.

The result is rejected if the role row is updated, deleted and recreated, the
credential rotates, the user or membership becomes inactive, the Workspace is
archived or changes generation, or the installed binding drifts. Runtime
resolution requires the same digest and freezes the employee role, override and
credential identities into the invocation selection digest. Provider response
identity must still match exactly.

Model-name fields reject secret-shaped strings, authenticated URLs, sensitive
environment assignments, `.env` locators and absolute physical paths before
persistence or Audit. Probe and personal Runtime use the same hardened HTTPS
client: allowlisted public DNS only, port 443, no environment proxy, no
redirects and a frozen address set while TLS keeps the original hostname. Each
Runtime dispatch resolves the policy anew; a changed allowlist or DNS set
requires a fresh probe. This does not claim protection against compromise of
the authoritative DNS or Provider endpoint itself.

## Migration and recovery

Migration `0016` adds `workspace_agent_model_overrides` in tenant scope and a
composite `(id, user_id)` unique key on saved credentials so the override's
composite foreign key enforces user ownership. The table contains no secret
bytes. Populated tenant downgrade refuses. The exact `0016 -> 0015` online
downgrade runs every retained tenant first and the global revision last in one
transaction. Its final global preflight requires every tenant head to equal
`0015` and requires both the override table and the added credential unique
constraint to be absent; global-first or partial downgrade attempts fail
closed without moving any head.

The personal target, backup and restore-new contracts advance to exact head
`0016`, bind the raw migration bytes and accept only the closed `0015 -> 0016`
forward compatibility entry. `0017+` remains unreviewed and rejected. Historical
`0015` evidence is not rewritten.

P34.7 acknowledges the current migration fact only. Its Trust Policy remains
unapproved, production evidence remains unauthorized and all production feature
gates remain false.

## Recovery

On an unsafe setting, delete the exact versioned override or restore inheritance.
On migration or restore drift, keep Runtime disabled and use a forward fix or a
new `omnibase_restore_*` database. Never reveal/copy a key, accept stale test
evidence, downgrade populated data, or enable Planner/Multi-Agent as a repair.
