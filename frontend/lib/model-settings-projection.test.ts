import assert from 'node:assert/strict'
import test from 'node:test'

import {
  advanceModelSettingsScope,
  captureModelSettingPreparation,
  modelSettingPreparationIsCurrent,
  modelSettingsScopeKey,
  projectionForScope,
  type ModelSettingsProjection,
  type ModelSettingsScope,
} from './model-settings-projection'
import type { AgentModelSettingRead } from './types'

function setting(version = 1, model = 'deepseek-v4'): AgentModelSettingRead {
  return {
    employee_role_id: 'parent',
    inherit_default: false,
    override_credential_id: 'credential-1',
    requested_model_id: model,
    effective_provider_id: 'relay',
    effective_model_id: model,
    family: 'deepseek',
    family_source: 'model_name',
    state: 'active',
    test_status: 'passed',
    tested_at: '2026-08-13T00:00:00Z',
    version,
  }
}

test('rapid Workspace and Agent switches synchronously reject late projections', () => {
  let scope: ModelSettingsScope = { key: null, generation: 0 }
  scope = advanceModelSettingsScope(scope, modelSettingsScopeKey('workspace-a', 'agent-a'))
  const firstA: ModelSettingsProjection = { scope, items: [setting()] }
  scope = advanceModelSettingsScope(scope, modelSettingsScopeKey('workspace-b', 'agent-b'))
  assert.equal(projectionForScope(firstA, scope), null)
  const B: ModelSettingsProjection = { scope, items: [setting(1, 'kimi-k3')] }
  scope = advanceModelSettingsScope(scope, modelSettingsScopeKey('workspace-a', 'agent-a'))
  assert.equal(projectionForScope(firstA, scope), null)
  assert.equal(projectionForScope(B, scope), null)
  const secondA: ModelSettingsProjection = { scope, items: [setting(2)] }
  assert.deepEqual(projectionForScope(secondA, scope)?.items, [setting(2)])
})

test('preparation CAS binds loaded key, scope generation, role and setting identity', () => {
  const scope = advanceModelSettingsScope(
    { key: null, generation: 0 },
    modelSettingsScopeKey('workspace-a', 'agent-a'),
  )
  const projection: ModelSettingsProjection = { scope, items: [setting()] }
  const snapshot = captureModelSettingPreparation(projection, 'parent')
  assert.ok(snapshot)
  assert.equal(modelSettingPreparationIsCurrent(snapshot, projection, scope), true)
  assert.equal(
    modelSettingPreparationIsCurrent(snapshot, { scope, items: [setting(2)] }, scope),
    false,
  )
  const returnedScope = advanceModelSettingsScope(
    advanceModelSettingsScope(scope, modelSettingsScopeKey('workspace-b', 'agent-b')),
    modelSettingsScopeKey('workspace-a', 'agent-a'),
  )
  assert.equal(modelSettingPreparationIsCurrent(snapshot, projection, returnedScope), false)
})
