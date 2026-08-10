import assert from 'node:assert/strict'
import test from 'node:test'
import {
  agentInvokeConditionsMet,
  canInvokeAgent,
  personalRuntimeInvokeConditionsMet,
  type PersonalRuntimeInvokePosture,
} from './personal-runtime-gate'

const PERSONAL: PersonalRuntimeInvokePosture = {
  engineering_assembled: false,
  environment_allowed: false,
  lite_gate_enabled: false,
  phase5_gates_all_false: false,
  production_activation_allowed: true,
  runtime_profile: 'personal_single_owner',
  personal_runtime_state: 'active',
  personal_runtime_active: true,
  tools_enabled: false,
  multi_agent_enabled: false,
  supported_invocation_modes: ['no_tool'],
}

test('exact personal canary posture opens the no-tool workbench', () => {
  assert.equal(personalRuntimeInvokeConditionsMet(PERSONAL), true)
  assert.equal(agentInvokeConditionsMet(PERSONAL), true)
  assert.equal(canInvokeAgent(PERSONAL, 'hello', 'workspace', 'agent-version'), true)
})

test('personal canary posture is an exact fail-closed conjunction', () => {
  assert.equal(personalRuntimeInvokeConditionsMet(null), false)
  assert.equal(personalRuntimeInvokeConditionsMet(undefined), false)
  assert.equal(
    personalRuntimeInvokeConditionsMet({ ...PERSONAL, runtime_profile: 'enterprise_governed' }),
    false,
  )
  assert.equal(
    personalRuntimeInvokeConditionsMet({ ...PERSONAL, personal_runtime_state: 'expired' }),
    false,
  )
  assert.equal(
    personalRuntimeInvokeConditionsMet({ ...PERSONAL, personal_runtime_active: false }),
    false,
  )
  assert.equal(
    personalRuntimeInvokeConditionsMet({ ...PERSONAL, production_activation_allowed: false }),
    false,
  )
  assert.equal(personalRuntimeInvokeConditionsMet({ ...PERSONAL, tools_enabled: true }), false)
  assert.equal(
    personalRuntimeInvokeConditionsMet({ ...PERSONAL, multi_agent_enabled: true }),
    false,
  )
  assert.equal(
    personalRuntimeInvokeConditionsMet({
      ...PERSONAL,
      supported_invocation_modes: ['no_tool', 'knowledge_search'],
    }),
    false,
  )
})

test('engineering Lite posture remains supported without weakening its four-way gate', () => {
  const lite: PersonalRuntimeInvokePosture = {
    ...PERSONAL,
    engineering_assembled: true,
    environment_allowed: true,
    lite_gate_enabled: true,
    phase5_gates_all_false: true,
    production_activation_allowed: false,
    runtime_profile: 'engineering_lite',
    personal_runtime_state: 'inactive',
    personal_runtime_active: false,
  }
  assert.equal(personalRuntimeInvokeConditionsMet(lite), false)
  assert.equal(agentInvokeConditionsMet(lite), true)
  assert.equal(canInvokeAgent(lite, 'hello', 'workspace', 'agent-version'), true)
})

test('both lanes still require complete user interface context', () => {
  assert.equal(canInvokeAgent(PERSONAL, '', 'workspace', 'agent-version'), false)
  assert.equal(canInvokeAgent(PERSONAL, '   ', 'workspace', 'agent-version'), false)
  assert.equal(canInvokeAgent(PERSONAL, 'hello', '', 'agent-version'), false)
  assert.equal(canInvokeAgent(PERSONAL, 'hello', 'workspace', ''), false)
})
