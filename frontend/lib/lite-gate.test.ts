import assert from 'node:assert/strict'
import test from 'node:test'
import { canInvokeLiteAgent, liteInvokeConditionsMet, type LiteInvokePosture } from './lite-gate'

const OPEN_POSTURE: LiteInvokePosture = {
  lite_gate_enabled: true,
  engineering_assembled: true,
  environment_allowed: true,
  phase5_gates_all_false: true,
}

test('all four conditions plus a complete UI context are required together', () => {
  assert.equal(canInvokeLiteAgent(OPEN_POSTURE, 'hello', 'ws-1', 'av-1'), true)
})

test('posture conditions require all four gates simultaneously', () => {
  assert.equal(liteInvokeConditionsMet(OPEN_POSTURE), true)
  assert.equal(liteInvokeConditionsMet(null), false)
  assert.equal(liteInvokeConditionsMet(undefined), false)
  assert.equal(liteInvokeConditionsMet({ ...OPEN_POSTURE, lite_gate_enabled: false }), false)
  assert.equal(liteInvokeConditionsMet({ ...OPEN_POSTURE, engineering_assembled: false }), false)
  assert.equal(liteInvokeConditionsMet({ ...OPEN_POSTURE, environment_allowed: false }), false)
  assert.equal(liteInvokeConditionsMet({ ...OPEN_POSTURE, phase5_gates_all_false: false }), false)
})

test('every single-condition shortcut is rejected (fail closed)', () => {
  assert.equal(canInvokeLiteAgent(null, 'hello', 'ws-1', 'av-1'), false)
  assert.equal(canInvokeLiteAgent(undefined, 'hello', 'ws-1', 'av-1'), false)

  // Lite gate closed but everything else open.
  assert.equal(
    canInvokeLiteAgent({ ...OPEN_POSTURE, lite_gate_enabled: false }, 'hello', 'ws-1', 'av-1'),
    false,
  )
  // Alpha not assembled in this environment.
  assert.equal(
    canInvokeLiteAgent({ ...OPEN_POSTURE, engineering_assembled: false }, 'hello', 'ws-1', 'av-1'),
    false,
  )
  // Environment not allowed (e.g. production).
  assert.equal(
    canInvokeLiteAgent({ ...OPEN_POSTURE, environment_allowed: false }, 'hello', 'ws-1', 'av-1'),
    false,
  )
  // A production Phase 5 gate flipped on.
  assert.equal(
    canInvokeLiteAgent({ ...OPEN_POSTURE, phase5_gates_all_false: false }, 'hello', 'ws-1', 'av-1'),
    false,
  )
})

test('incomplete UI context is rejected even with an open posture', () => {
  assert.equal(canInvokeLiteAgent(OPEN_POSTURE, '', 'ws-1', 'av-1'), false)
  assert.equal(canInvokeLiteAgent(OPEN_POSTURE, '   ', 'ws-1', 'av-1'), false)
  assert.equal(canInvokeLiteAgent(OPEN_POSTURE, 'hello', '', 'av-1'), false)
  assert.equal(canInvokeLiteAgent(OPEN_POSTURE, 'hello', 'ws-1', ''), false)
  assert.equal(canInvokeLiteAgent(OPEN_POSTURE, 'hello', 'ws-1', 'av-1'), true)
})
