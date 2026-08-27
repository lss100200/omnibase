import assert from 'node:assert/strict'
import test from 'node:test'

import {
  P6_GEAR_PROFILES,
  buildP6AdaptationInstruction,
  compileP6Context,
  estimateP6Cost,
  getP6ProviderProfile,
  resolveP6ModelFamily,
  resolveP6ProviderFamily,
} from './p6-model-profiles'

test('five target provider families and a generic fallback resolve deterministically', () => {
  assert.equal(
    resolveP6ProviderFamily({ providerId: 'deepseek', modelId: 'deepseek-chat' }),
    'deepseek',
  )
  assert.equal(resolveP6ProviderFamily({ providerId: 'zhipu', modelId: 'glm-5.2' }), 'glm')
  assert.equal(resolveP6ProviderFamily({ providerId: 'moonshot', modelId: 'kimi-k2' }), 'kimi')
  assert.equal(resolveP6ProviderFamily({ providerId: 'openai', modelId: 'gpt-5' }), 'openai')
  assert.equal(
    resolveP6ProviderFamily({ providerId: 'anthropic', modelId: 'claude-opus-5' }),
    'anthropic',
  )
  assert.equal(resolveP6ProviderFamily({ providerId: 'custom', modelId: 'local-model' }), 'generic')
})

test('model name wins over an unrelated relay URL and conflicts fail closed', () => {
  assert.deepEqual(
    resolveP6ModelFamily({
      providerId: 'custom-relay',
      baseUrl: 'https://random.example/openai/v1',
      modelId: 'deepseek-v4-pro',
    }),
    {
      family: 'deepseek',
      source: 'model_name',
      confidence: 'strong',
      matchedTokens: ['deepseek'],
      conflicts: [],
    },
  )
  const conflict = resolveP6ModelFamily({ modelId: 'claude-gpt-bridge', providerId: null })
  assert.equal(conflict.family, 'generic')
  assert.deepEqual(conflict.conflicts, ['openai', 'anthropic'])
})

test('GLM and Claude exact names resolve consistently through unrelated relays', () => {
  for (const modelId of ['glm-5.2', 'zhipu/glm-5.2', 'relay/glm-4.7-flashx']) {
    assert.equal(
      resolveP6ProviderFamily({
        providerId: 'anthropic-relay',
        baseUrl: 'https://relay.example/claude/v1',
        modelId,
      }),
      'glm',
    )
  }
  for (const modelId of [
    'claude-opus-5',
    'anthropic/claude-sonnet-5',
    'anthropic/sonnet-5',
    'relay/claude-haiku-4-5',
  ]) {
    assert.equal(
      resolveP6ProviderFamily({
        providerId: 'zhipu-relay',
        baseUrl: 'https://relay.example/glm/v1',
        modelId,
      }),
      'anthropic',
    )
  }
})

test('Kimi and Moonshot exact names resolve consistently through unrelated relays', () => {
  for (const modelId of ['kimi-k2', 'moonshot-v1-128k']) {
    assert.equal(
      resolveP6ProviderFamily({
        providerId: 'openai-relay',
        baseUrl: 'https://relay.example/gpt/v1',
        modelId,
      }),
      'kimi',
    )
  }
})

test('bare, proxy, conflicting and unknown model names fail closed', () => {
  for (const modelId of [
    'glm',
    'chatglm',
    'kimi',
    'moonshot',
    'claude',
    'anthropic',
    'sonnet-5',
    'proxy/claude-opus-5',
    'proxy/kimi-k2',
    'glm-5.2-claude-sonnet-5',
    'kimi-k2-gpt-5',
  ]) {
    assert.equal(resolveP6ModelFamily({ providerId: null, modelId }).family, 'generic')
  }
  assert.equal(
    resolveP6ProviderFamily({ providerId: 'custom-relay', modelId: 'unknown-model' }),
    'generic',
  )
})

test('non-empty unknown requested or observed names block branded URL and provider hints', () => {
  assert.deepEqual(
    resolveP6ModelFamily({
      providerId: 'moonshot',
      baseUrl: 'https://api.moonshot.cn/v1',
      modelId: 'unknown-model',
    }),
    {
      family: 'generic',
      source: 'model_name',
      confidence: 'unknown',
      matchedTokens: [],
      conflicts: [],
    },
  )
  assert.deepEqual(
    resolveP6ModelFamily({
      providerId: 'anthropic',
      baseUrl: 'https://api.anthropic.com/v1',
      modelId: null,
      observedModelId: 'unknown-observed-model',
    }),
    {
      family: 'generic',
      source: 'observed_model',
      confidence: 'unknown',
      matchedTokens: [],
      conflicts: [],
    },
  )
  assert.equal(
    resolveP6ModelFamily({
      providerId: 'moonshot',
      baseUrl: 'https://api.moonshot.cn/v1',
      modelId: 'kimi-k2',
      observedModelId: 'unknown-observed-model',
    }).family,
    'generic',
  )
})

test('requested and observed family conflicts fail closed before provider hints', () => {
  assert.deepEqual(
    resolveP6ModelFamily({
      providerId: 'moonshot',
      baseUrl: 'https://api.moonshot.cn/v1',
      modelId: 'kimi-k2',
      observedModelId: 'gpt-5',
    }),
    {
      family: 'generic',
      source: 'observed_model',
      confidence: 'unknown',
      matchedTokens: [],
      conflicts: ['kimi', 'openai'],
    },
  )
})

test('all profiles disclose that native tools and reasoning controls remain closed', () => {
  for (const modelId of [
    'deepseek-chat',
    'glm-5.2',
    'kimi-k2',
    'gpt-5',
    'claude-opus-5',
    'custom',
  ]) {
    const profile = getP6ProviderProfile({ providerId: null, modelId })
    assert.equal(profile.nativeReasoningControl, false)
    assert.equal(profile.toolsEnabled, false)
    assert.equal(profile.mcpEnabled, false)
    assert.equal(profile.cliEnabled, false)
  }
})

test('four gears form a closed increasing context and retrieval profile', () => {
  assert.deepEqual(Object.keys(P6_GEAR_PROFILES), ['economy', 'standard', 'deep', 'audit'])
  assert.equal(P6_GEAR_PROFILES.economy.topK, 2)
  assert.equal(P6_GEAR_PROFILES.standard.topK, 5)
  assert.equal(P6_GEAR_PROFILES.deep.topK, 8)
  assert.equal(P6_GEAR_PROFILES.audit.topK, 8)
  assert.ok(
    P6_GEAR_PROFILES.economy.contextCharacterBudget < P6_GEAR_PROFILES.audit.contextCharacterBudget,
  )
})

test('adaptation text is honest about prompt guidance and unavailable native controls', () => {
  const value = buildP6AdaptationInstruction(
    { providerId: 'anthropic', modelId: 'claude-sonnet' },
    'audit',
  )
  assert.match(value, /Claude/)
  assert.match(value, /审计挡/)
  assert.match(value, /Research profile: 2026-08-14/)
  assert.match(value, /does not prove native reasoning, vision, tool, cache or schema support/)
  assert.match(value, /does not claim Anthropic Messages controls or unprobed GLM extensions/)
  assert.match(value, /Tools, MCP, CLI and autonomous delegation remain disabled/)
})

test('GLM and Claude profiles do not claim unproved native transport controls', () => {
  for (const modelId of ['glm-5.2', 'claude-opus-5']) {
    const profile = getP6ProviderProfile({ providerId: null, modelId })
    assert.equal(profile.researchVersion, '2026-08-14')
    assert.equal(profile.reasoning, 'unknown')
    assert.equal(profile.reasoningContinuationRequired, 'unknown')
    assert.equal(profile.structuredOutput, 'unknown')
    assert.equal(profile.promptCaching, 'unknown')
  }
})

test('context compilation preserves priority and deterministically omits lower value context', () => {
  const result = compileP6Context(
    [
      { id: 'support', label: 'support', content: 's'.repeat(30), priority: 'supporting' },
      { id: 'pin', label: 'pin', content: 'p'.repeat(30), priority: 'pinned' },
      { id: 'owner', label: 'owner', content: 'o'.repeat(30), priority: 'owner_explicit' },
    ],
    110,
  )
  assert.equal(result.ok, true)
  if (!result.ok) return
  assert.deepEqual(result.includedIds, ['owner', 'pin'])
  assert.deepEqual(result.omittedIds, ['support'])
})

test('required context fails closed instead of being truncated', () => {
  const result = compileP6Context(
    [
      {
        id: 'required',
        label: 'required',
        content: 'x'.repeat(100),
        priority: 'owner_explicit',
        required: true,
      },
    ],
    20,
  )
  assert.deepEqual(result, {
    ok: false,
    code: 'required_context_exceeds_budget',
    candidateId: 'required',
    budgetCharacters: 20,
  })
})

test('duplicate context identifiers and invalid budgets fail closed', () => {
  assert.equal(
    compileP6Context(
      [
        { id: 'same', label: 'a', content: 'a', priority: 'open' },
        { id: 'same', label: 'b', content: 'b', priority: 'open' },
      ],
      100,
    ).ok,
    false,
  )
  assert.equal(compileP6Context([], -1).ok, false)
})

test('cost stays unknown without an explicit rate and computes only from supplied rates', () => {
  assert.deepEqual(estimateP6Cost({ inputTokens: 1000, outputTokens: 500 }), {
    known: false,
    reason: 'rate_not_configured',
  })
  const result = estimateP6Cost(
    { inputTokens: 1_000_000, outputTokens: 500_000, reasoningTokens: 250_000 },
    { inputPerMillion: 1, outputPerMillion: 2, reasoningPerMillion: 4, currency: 'USD' },
  )
  assert.equal(result.known, true)
  if (!result.known) return
  assert.equal(result.amount, 3)
})

test('negative or non-finite usage never produces a monetary claim', () => {
  assert.deepEqual(estimateP6Cost({ inputTokens: -1, outputTokens: 0 }), {
    known: false,
    reason: 'invalid_usage',
  })
})
