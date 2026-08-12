import assert from 'node:assert/strict'
import test from 'node:test'

import {
  P6_GEAR_PROFILES,
  buildP6AdaptationInstruction,
  compileP6Context,
  estimateP6Cost,
  getP6ProviderProfile,
  resolveP6ProviderFamily,
} from './p6-model-profiles'

test('five target provider families and a generic fallback resolve deterministically', () => {
  assert.equal(
    resolveP6ProviderFamily({ providerId: 'deepseek', modelId: 'deepseek-chat' }),
    'deepseek',
  )
  assert.equal(resolveP6ProviderFamily({ providerId: 'zhipu', modelId: 'glm-4.5' }), 'glm')
  assert.equal(resolveP6ProviderFamily({ providerId: 'moonshot', modelId: 'kimi-k2' }), 'kimi')
  assert.equal(resolveP6ProviderFamily({ providerId: 'openai', modelId: 'gpt-5' }), 'gpt')
  assert.equal(resolveP6ProviderFamily({ providerId: 'anthropic', modelId: 'claude-4' }), 'claude')
  assert.equal(resolveP6ProviderFamily({ providerId: 'custom', modelId: 'local-model' }), 'generic')
})

test('all profiles disclose that native tools and reasoning controls remain closed', () => {
  for (const modelId of ['deepseek-chat', 'glm-4', 'kimi-k2', 'gpt-5', 'claude-4', 'custom']) {
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
  assert.match(value, /Native provider reasoning controls are not exposed/)
  assert.match(value, /Tools, MCP, CLI and autonomous delegation remain disabled/)
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
