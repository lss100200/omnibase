import assert from 'node:assert/strict'
import test from 'node:test'
import { P6_READONLY_MCP_TOOLS, summarizeP6ModelCapabilities } from './p6-capability-center'

test('capability summary distinguishes operator fallback and explicit role overrides', () => {
  const summary = summarizeP6ModelCapabilities(
    {
      credential_source: 'operator_default',
      provider_id: 'operator',
      model_id: 'model',
      credential_id: null,
    },
    [
      {
        employee_role_id: 'parent',
        inherit_default: true,
        override_credential_id: null,
        requested_model_id: null,
        effective_provider_id: 'operator',
        effective_model_id: 'model',
        family: 'generic',
        family_source: 'unknown',
        state: 'inherited',
        test_status: null,
        tested_at: null,
        version: 0,
      },
      {
        employee_role_id: 'security',
        inherit_default: false,
        override_credential_id: 'credential',
        requested_model_id: 'deepseek-reasoner',
        effective_provider_id: 'deepseek',
        effective_model_id: 'deepseek-reasoner',
        family: 'deepseek',
        family_source: 'model_name',
        state: 'pending',
        test_status: null,
        tested_at: null,
        version: 1,
      },
    ],
  )
  assert.equal(summary.defaultRuntimeSource, 'operator_default')
  assert.equal(summary.readyRoles, 1)
  assert.equal(summary.pendingRoles, 1)
  assert.equal(summary.explicitOverrides, 1)
})

test('MCP preview remains an exact six-tool read-only closed set', () => {
  assert.deepEqual(
    P6_READONLY_MCP_TOOLS.map((tool) => tool.id),
    [
      'omnibase_files_list',
      'omnibase_files_read',
      'omnibase_git_inspect',
      'omnibase_files_hash',
      'omnibase_text_search',
      'omnibase_git_diff_summary',
    ],
  )
})
