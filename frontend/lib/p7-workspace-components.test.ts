import assert from 'node:assert/strict'
import { test } from 'node:test'

import type { DesktopWorkspaceComponentSnapshot } from './desktop-bridge'

import {
  createP7WorkspaceComponentSurfaceState,
  p7AssistantDeclarativePackagePrompt,
  p7ComponentActionEligibility,
  p7ComponentEffectNeedsReconciliation,
  p7ComponentInvocationEligible,
  p7DefaultWorkspaceComponentGrant,
  p7DeclarativeSettingsDefaults,
  p7DeclarativeSettingsDiff,
  p7EmergencyStopEligible,
  p7EnterWorkspaceComponentSafeMode,
  p7FindNewCompletedComponentAssistantMessage,
  p7ParseAssistantDeclarativePackage,
  p7ParseDeclarativeSettingsSchema,
  p7ParseWorkspaceComponentSurface,
  p7ReconcileWorkspaceComponentSurfaces,
  p7SetWorkspaceComponentSurface,
  p7ValidateDeclarativeSettings,
  p7WorkspaceComponentGrantMatchesCatalog,
  p7WorkspaceComponentResultEventLogLine,
  p7WorkspaceComponentAssistantPrompt,
  p7WorkspaceComponentHostSlotId,
  p7WorkspaceComponentLifecycleActions,
  p7WorkspaceComponentHostProjection,
  p7WorkspaceComponentCommittedUiBindings,
  p7WorkspaceComponentSurfaceProjection,
  p7WorkspaceComponentSurfaceRequests,
  p7WorkspaceComponentsProjection,
  p7WorkspaceComponentVersionChangeAction,
} from './p7-workspace-components'

const WORKSPACE_A = `workspace_${'a'.repeat(32)}`
const WORKSPACE_B = `workspace_${'b'.repeat(32)}`
const REQUEST_SHA = 'a'.repeat(64)
const OPERATION_ID = `operation_${'a'.repeat(32)}`

test('component lifecycle requires an attested package and covers every transition', () => {
  const catalog = {
    available: true,
    manifestSha256: '2'.repeat(64),
    packageSha256: '3'.repeat(64),
    version: '2.0.0',
  }
  assert.deepEqual(p7WorkspaceComponentLifecycleActions(catalog, null), ['install'])
  assert.deepEqual(
    p7WorkspaceComponentLifecycleActions(catalog, { state: 'installed', version: '2.0.0' }),
    ['bind', 'revoke'],
  )
  assert.deepEqual(
    p7WorkspaceComponentLifecycleActions(catalog, { state: 'bound', version: '2.0.0' }),
    ['activate', 'revoke'],
  )
  assert.deepEqual(
    p7WorkspaceComponentLifecycleActions(catalog, { state: 'active', version: '2.0.0' }),
    ['disable', 'revoke'],
  )
  assert.deepEqual(
    p7WorkspaceComponentLifecycleActions(catalog, { state: 'disabled', version: '2.0.0' }),
    ['activate', 'revoke', 'uninstall'],
  )
  assert.deepEqual(
    p7WorkspaceComponentLifecycleActions(catalog, { state: 'revoked', version: '2.0.0' }),
    ['uninstall'],
  )
  assert.deepEqual(
    p7WorkspaceComponentLifecycleActions(catalog, { state: 'blocked', version: '2.0.0' }),
    [],
  )
  assert.deepEqual(
    p7WorkspaceComponentLifecycleActions(
      { ...catalog, available: false, manifestSha256: null, packageSha256: null },
      null,
    ),
    [],
  )
  assert.equal(p7WorkspaceComponentVersionChangeAction('1.9.9', '2.0.0'), 'upgrade')
  assert.equal(p7WorkspaceComponentVersionChangeAction('2.0.0', '1.9.9'), 'rollback')
  assert.equal(p7WorkspaceComponentVersionChangeAction('2.0', '2.1.0'), null)
})

test('component proposal defaults stay inside manifest resource and service classes', () => {
  const catalog: Parameters<typeof p7DefaultWorkspaceComponentGrant>[0] = {
    componentId: 'builtin.readonly-mcp',
    version: '1.0.0',
    family: 'mcp_connector',
    displayName: 'Read-only MCP Connector',
    publisherClass: 'source_owned',
    adapterId: 'readonly-mcp.v1',
    policyManifestSha256: '1'.repeat(64),
    manifestSha256: '2'.repeat(64),
    packageSha256: '3'.repeat(64),
    operations: ['mcp.call'],
    permissions: [
      {
        action: 'mcp.call',
        dataScope: 'workspace_logical',
        logicalResourceClasses: ['workspace.component.input'],
        secretReferenceClasses: [],
      },
    ],
    slots: [],
    dependencies: [],
    conflicts: [],
    budgets: {
      maxCalls: 8,
      maxBytesIn: 1_024,
      maxBytesOut: 2_048,
      maxTokens: 0,
      maxWallTimeMs: 5_000,
      maxCostUnits: 4,
      maxRetries: 0,
      maxConcurrency: 1,
    },
    network: { required: true, serviceClasses: ['reviewed_https'] },
    recovery: {
      autoReplayUnknown: false,
      retention: 'retain_workspace_data',
      safeMode: 'disable_component',
    },
    stateSchema: { kind: 'canonical_json', version: 1 },
    settingsSchema: {
      kind: 'closed_object',
      version: 1,
      additionalProperties: false,
      properties: {},
      required: [],
    },
    available: true,
    unavailableReason: null,
  }
  const grant = p7DefaultWorkspaceComponentGrant(catalog, 'mcp.call')
  assert.ok(grant)
  assert.equal(grant.logicalResourceId, 'workspace.component.input')
  assert.equal(grant.resourceVersion, 1)
  assert.equal(grant.logicalServiceId, 'reviewed_https')
  assert.equal(p7WorkspaceComponentGrantMatchesCatalog(catalog, grant), true)
  assert.equal(
    p7WorkspaceComponentGrantMatchesCatalog(catalog, {
      ...grant,
      logicalResourceId: 'workspace.component.other',
    }),
    false,
  )
  assert.equal(
    p7WorkspaceComponentGrantMatchesCatalog(catalog, {
      ...grant,
      logicalServiceId: 'ambient_network',
    }),
    false,
  )
})

test('component Agent prompt exposes only available exact identities and never grants authority', () => {
  const prompt = p7WorkspaceComponentAssistantPrompt('Install the canvas for review', {
    workspaceId: WORKSPACE_A,
    catalog: [
      {
        componentId: 'builtin.workspace-canvas',
        version: '1.0.0',
        family: 'declarative_ui',
        policyManifestSha256: '1'.repeat(64),
        manifestSha256: '2'.repeat(64),
        packageSha256: '3'.repeat(64),
        available: true,
        operations: ['ui.render'],
        permissions: [
          {
            action: 'ui.render',
            dataScope: 'workspace_logical',
            logicalResourceClasses: ['workspace.component.input'],
            secretReferenceClasses: [],
          },
        ],
        slots: [
          {
            slotId: 'editor.component',
            cardinality: 'one',
            minimumOrder: 0,
            maximumOrder: 0,
          },
        ],
        dependencies: [],
        settingsSchema: {
          kind: 'closed_object',
          version: 1,
          additionalProperties: false,
          properties: {},
          required: [],
        },
        budgets: {
          maxCalls: 1,
          maxBytesIn: 0,
          maxBytesOut: 1024,
          maxTokens: 0,
          maxWallTimeMs: 1000,
          maxCostUnits: 1,
        },
        network: { required: false, serviceClasses: [] },
      },
      {
        componentId: 'unavailable.component',
        version: '1.0.0',
        family: 'declarative_ui',
        policyManifestSha256: '4'.repeat(64),
        manifestSha256: null,
        packageSha256: null,
        available: false,
        operations: ['ui.render'],
        permissions: [
          {
            action: 'ui.render',
            dataScope: 'none',
            logicalResourceClasses: [],
            secretReferenceClasses: [],
          },
        ],
        slots: [],
        dependencies: [],
        settingsSchema: {},
        budgets: {
          maxCalls: 1,
          maxBytesIn: 0,
          maxBytesOut: 0,
          maxTokens: 0,
          maxWallTimeMs: 1,
          maxCostUnits: 1,
        },
        network: { required: false, serviceClasses: [] },
      },
    ],
    installations: [],
  })
  assert.ok(prompt)
  assert.match(prompt, /policy_manifest_sha256/)
  assert.match(prompt, new RegExp('1'.repeat(64)))
  assert.match(prompt, new RegExp('2'.repeat(64)))
  assert.match(prompt, new RegExp('3'.repeat(64)))
  assert.doesNotMatch(prompt, /unavailable\.component/)
  assert.match(prompt, /Do not approve, install, execute, reconcile/)
  assert.equal(
    p7WorkspaceComponentAssistantPrompt(' ', {
      workspaceId: WORKSPACE_A,
      catalog: [],
      installations: [],
    }),
    null,
  )
})

test('component Agent proposal selects only a fresh completed successful assistant message', () => {
  const old = {
    id: `message_${'1'.repeat(32)}`,
    role: 'assistant' as const,
    content: '{}',
    status: 'completed',
    invocationId: `invocation_${'1'.repeat(32)}`,
    invocation: { id: `invocation_${'1'.repeat(32)}`, status: 'succeeded' },
  }
  const fresh = {
    ...old,
    id: `message_${'2'.repeat(32)}`,
    invocationId: `invocation_${'2'.repeat(32)}`,
    invocation: { id: `invocation_${'2'.repeat(32)}`, status: 'succeeded' },
  }
  assert.equal(
    p7FindNewCompletedComponentAssistantMessage([old, fresh], new Set([old.id]))?.id,
    fresh.id,
  )
  assert.equal(
    p7FindNewCompletedComponentAssistantMessage(
      [{ ...fresh, invocation: { ...fresh.invocation, status: 'failed' } }],
      new Set(),
    ),
    null,
  )
})

function assistantDeclarativePackage(): Record<string, unknown> {
  return {
    schema_version: 1,
    manifest: {
      manifest_schema_version: 1,
      component_id: 'owner.focus-board',
      family: 'declarative_ui',
      version: '1.0.0',
      publisher: { classification: 'owner_reviewed', id: 'owner.local' },
      compatibility: { desktop_schema_min: 11, host_api: 'p7.3.v1' },
      entrypoint: { adapter_id: 'builtin-ui.v1', kind: 'host_view_v1' },
      operations: ['ui.render'],
      slots: [
        {
          slot_id: 'editor.component',
          cardinality: 'one',
          minimum_order: 0,
          maximum_order: 100,
        },
      ],
      dependencies: [],
      conflicts: [],
      configuration_schema: {
        kind: 'closed_object',
        version: 1,
        additional_properties: false,
        properties: {},
        required: [],
      },
      permissions: [
        {
          action: 'ui.render',
          data_scope: 'none',
          logical_resource_classes: [],
          secret_reference_classes: [],
        },
      ],
      network: { required: false, service_classes: [] },
      budgets: {
        max_calls: 1,
        max_concurrency: 1,
        max_retries: 0,
        max_wall_time_ms: 5_000,
        max_bytes_in: 1_024,
        max_bytes_out: 1_024,
        max_tokens: 0,
        max_cost_units: 0,
      },
      health: { kind: 'native_receipt_v1', required_state: 'healthy', timeout_ms: 5_000 },
      quiesce_timeout_ms: 5_000,
      state_schema: { kind: 'canonical_json', version: 1 },
      state_migration: {
        kind: 'host_canonical_v1',
        requires_owner_review_on_schema_change: true,
      },
      recovery: {
        auto_replay_unknown: false,
        retention: 'retain_workspace_data',
        safe_mode: 'disable_component',
      },
      uninstall: { retention: 'retain_workspace_data', unbound_delete_forbidden: true },
    },
    view: {
      kind: 'workspace_summary',
      title: 'Focus board',
      sections: [
        { id: 'runtime', label: 'Runtime', source: 'installation' },
        { id: 'health', label: 'Health', source: 'health' },
      ],
    },
  }
}

function successfulPackageMessage(content: string) {
  const invocationId = `invocation_${'7'.repeat(32)}`
  return {
    id: `message_${'7'.repeat(32)}`,
    role: 'assistant' as const,
    content,
    status: 'completed',
    invocationId,
    invocation: { id: invocationId, status: 'succeeded' },
  }
}

test('assistant declarative package parses to deterministic exact identities and scope', async () => {
  const value = assistantDeclarativePackage()
  const first = await p7ParseAssistantDeclarativePackage({
    workspaceId: WORKSPACE_A,
    conversationId: 'conversation_a',
    message: successfulPackageMessage(JSON.stringify(value)),
  })
  const reordered = await p7ParseAssistantDeclarativePackage({
    workspaceId: WORKSPACE_A,
    conversationId: 'conversation_a',
    message: successfulPackageMessage(
      JSON.stringify(Object.fromEntries(Object.entries(value).reverse())),
    ),
  })
  assert.ok(first)
  assert.ok(reordered)
  assert.equal(first.workspaceId, WORKSPACE_A)
  assert.equal(first.conversationId, 'conversation_a')
  assert.equal(first.componentId, 'owner.focus-board')
  assert.equal(first.publisherId, 'owner.local')
  assert.deepEqual(first.slots, ['editor.component'])
  assert.deepEqual(first.sections, [
    { id: 'runtime', label: 'Runtime', source: 'installation' },
    { id: 'health', label: 'Health', source: 'health' },
  ])
  assert.match(first.manifestSha256, /^[a-f0-9]{64}$/u)
  assert.equal(first.manifestSha256, reordered.manifestSha256)
  assert.equal(first.packageSha256, reordered.packageSha256)
  assert.equal(first.packageJson, reordered.packageJson)
})

test('assistant declarative package rejects unknown and forbidden authority fields', async () => {
  const unknown = assistantDeclarativePackage()
  unknown.automatic_install = true
  assert.equal(
    await p7ParseAssistantDeclarativePackage({
      workspaceId: WORKSPACE_A,
      conversationId: 'conversation_a',
      message: successfulPackageMessage(JSON.stringify(unknown)),
    }),
    null,
  )
  for (const title of [
    'https://unreviewed.invalid',
    'C:\\physical\\path',
    'run command',
    '<script>alert(1)</script>',
    'request credential',
  ]) {
    const forbidden = assistantDeclarativePackage()
    ;(forbidden.view as Record<string, unknown>).title = title
    assert.equal(
      await p7ParseAssistantDeclarativePackage({
        workspaceId: WORKSPACE_A,
        conversationId: 'conversation_a',
        message: successfulPackageMessage(JSON.stringify(forbidden)),
      }),
      null,
    )
  }
  const secretField = assistantDeclarativePackage()
  const configuration = (secretField.manifest as Record<string, unknown>)
    .configuration_schema as Record<string, unknown>
  configuration.properties = { secret_token: { type: 'string', max_length: 64 } }
  configuration.required = ['secret_token']
  assert.equal(
    await p7ParseAssistantDeclarativePackage({
      workspaceId: WORKSPACE_A,
      conversationId: 'conversation_a',
      message: successfulPackageMessage(JSON.stringify(secretField)),
    }),
    null,
  )
})

test('assistant declarative package rejects unsupported Slots and failed assistant identity', async () => {
  const unsupported = assistantDeclarativePackage()
  ;((unsupported.manifest as Record<string, unknown>).slots as Array<Record<string, unknown>>)[0] =
    {
      slot_id: 'terminal.component',
      cardinality: 'one',
      minimum_order: 0,
      maximum_order: 100,
    }
  assert.equal(
    await p7ParseAssistantDeclarativePackage({
      workspaceId: WORKSPACE_A,
      conversationId: 'conversation_a',
      message: successfulPackageMessage(JSON.stringify(unsupported)),
    }),
    null,
  )
  const successful = successfulPackageMessage(JSON.stringify(assistantDeclarativePackage()))
  assert.equal(
    await p7ParseAssistantDeclarativePackage({
      workspaceId: WORKSPACE_A,
      conversationId: 'conversation_a',
      message: { ...successful, invocation: { ...successful.invocation, status: 'failed' } },
    }),
    null,
  )
})

test('assistant declarative package prompt is bounded and denies automatic authority', () => {
  const prompt = p7AssistantDeclarativePackagePrompt('Create a quiet focus board')
  assert.ok(prompt)
  assert.ok(prompt.length <= 16_384)
  assert.match(prompt, /Return exactly one JSON object/)
  assert.match(prompt, /Do not install, approve, grant, activate or execute anything/)
  assert.equal(p7AssistantDeclarativePackagePrompt(' '), null)
  assert.equal(p7AssistantDeclarativePackagePrompt('x'.repeat(2_001)), null)
})

const schemaInput = {
  schemaVersion: 1,
  sections: [
    {
      id: 'connection',
      label: '连接',
      fields: [
        {
          id: 'enabled',
          label: '启用',
          control: 'boolean',
          required: true,
        },
        {
          id: 'mode',
          label: '模式',
          description: '受信模式闭集',
          control: 'select',
          required: true,
          options: [
            { value: 'read', label: '只读' },
            { value: 'review', label: '审阅' },
          ],
        },
        {
          id: 'secret',
          label: '凭据',
          control: 'secret-ref',
          required: false,
        },
      ],
    },
  ],
}

test('Workspace component projection drops the prior Workspace on the first frame', () => {
  const old = { workspaceId: WORKSPACE_A, revision: 1 }
  assert.deepEqual(
    p7WorkspaceComponentsProjection({
      loadedWorkspaceId: WORKSPACE_A,
      viewWorkspaceId: WORKSPACE_B,
      status: 'ready',
      snapshot: old,
    }),
    { status: 'loading', snapshot: null },
  )
  assert.equal(
    p7WorkspaceComponentsProjection({
      loadedWorkspaceId: WORKSPACE_A,
      viewWorkspaceId: WORKSPACE_A,
      status: 'ready',
      snapshot: old,
    }).snapshot,
    old,
  )
  assert.deepEqual(
    p7WorkspaceComponentsProjection({
      loadedWorkspaceId: WORKSPACE_A,
      viewWorkspaceId: WORKSPACE_A,
      status: 'ready',
      snapshot: { ...old, workspaceId: WORKSPACE_B },
    }),
    { status: 'error', snapshot: null },
  )
})

test('host declarative schema accepts only the closed controls and exact bounded shape', () => {
  const schema = p7ParseDeclarativeSettingsSchema(schemaInput)
  assert.notEqual(schema, null)
  assert.equal(
    p7ParseDeclarativeSettingsSchema({
      ...schemaInput,
      rendererScript: 'alert(1)',
    }),
    null,
  )
  assert.equal(
    p7ParseDeclarativeSettingsSchema({
      ...schemaInput,
      sections: [
        {
          ...schemaInput.sections[0],
          fields: [
            {
              id: 'unsafe',
              label: 'Unsafe',
              control: 'html',
              required: false,
            },
          ],
        },
      ],
    }),
    null,
  )
})

test('backend closed-object settings schema projects to bounded host controls', () => {
  const empty = p7ParseDeclarativeSettingsSchema({
    kind: 'closed_object',
    version: 2,
    additionalProperties: false,
    properties: {},
    required: [],
  })
  assert.deepEqual(empty, { schemaVersion: 1, sections: [] })

  const schema = p7ParseDeclarativeSettingsSchema({
    kind: 'closed_object',
    version: 2,
    additionalProperties: false,
    properties: {
      enabled: { type: 'boolean', default: true },
      retries: { type: 'integer', minimum: 0, maximum: 5, default: 2 },
      threshold: { type: 'number', minimum: 0, maximum: 1, default: 0.5 },
      mode: { type: 'string', enum: ['review', 'apply'], default: 'review' },
      note: { type: 'string', maxLength: 320 },
    },
    required: ['enabled', 'mode'],
  })
  assert.ok(schema)
  assert.deepEqual(p7DeclarativeSettingsDefaults(schema), {
    enabled: true,
    retries: 2,
    threshold: 0.5,
    mode: 'review',
  })
  assert.deepEqual(
    p7ValidateDeclarativeSettings(schema, {
      enabled: true,
      retries: 2,
      threshold: 0.5,
      mode: 'review',
    }),
    { valid: true, errors: {} },
  )
  assert.equal(
    p7ParseDeclarativeSettingsSchema({
      kind: 'closed_object',
      version: 2,
      additionalProperties: false,
      properties: { token: { type: 'string', maxLength: 32, physicalPath: true } },
      required: [],
    }),
    null,
  )
  assert.equal(
    p7ParseDeclarativeSettingsSchema({
      kind: 'closed_object',
      version: 2,
      additionalProperties: true,
      properties: {},
      required: [],
    }),
    null,
  )
})

test('declarative settings reject unknown keys, invalid options and physical-looking refs', () => {
  const schema = p7ParseDeclarativeSettingsSchema(schemaInput)
  assert.ok(schema)
  assert.deepEqual(p7ValidateDeclarativeSettings(schema, { enabled: true, mode: 'read' }), {
    valid: true,
    errors: {},
  })
  const invalid = p7ValidateDeclarativeSettings(schema, {
    enabled: true,
    mode: 'unsafe',
    secret: 'C:\\secret.txt',
    extra: true,
  })
  assert.equal(invalid.valid, false)
  assert.deepEqual(Object.keys(invalid.errors).sort(), ['extra', 'mode', 'secret'])
})

test('declarative diff is deterministic and never displays secret references', () => {
  const schema = p7ParseDeclarativeSettingsSchema(schemaInput)
  assert.ok(schema)
  assert.deepEqual(
    p7DeclarativeSettingsDiff(
      schema,
      { enabled: false, mode: 'read', secret: null },
      { enabled: true, mode: 'review', secret: 'vault.secret' },
    ),
    [
      {
        key: 'configuration.enabled',
        label: '启用',
        before: '关闭',
        after: '开启',
        sensitive: false,
      },
      {
        key: 'configuration.mode',
        label: '模式',
        before: '只读',
        after: '审阅',
        sensitive: false,
      },
      {
        key: 'configuration.secret',
        label: '凭据',
        before: '未绑定',
        after: '已绑定',
        sensitive: true,
      },
    ],
  )
})

test('lifecycle action requires same-Workspace exact approved non-stale proposal', () => {
  const proposal = {
    workspaceId: WORKSPACE_A,
    changeKind: 'activate' as const,
    decision: 'approved' as const,
    expectedRevision: 3,
    requestSha256: REQUEST_SHA,
  }
  assert.deepEqual(
    p7ComponentActionEligibility({
      viewWorkspaceId: WORKSPACE_A,
      snapshotWorkspaceId: WORKSPACE_A,
      snapshotRevision: 3,
      action: 'activate',
      installationState: 'bound',
      operationActive: false,
      proposal,
    }),
    { eligible: true, reason: 'eligible' },
  )
  assert.equal(
    p7ComponentActionEligibility({
      viewWorkspaceId: WORKSPACE_B,
      snapshotWorkspaceId: WORKSPACE_A,
      snapshotRevision: 3,
      action: 'activate',
      installationState: 'bound',
      operationActive: false,
      proposal,
    }).reason,
    'workspace-mismatch',
  )
  assert.equal(
    p7ComponentActionEligibility({
      viewWorkspaceId: WORKSPACE_A,
      snapshotWorkspaceId: WORKSPACE_A,
      snapshotRevision: 4,
      action: 'activate',
      installationState: 'bound',
      operationActive: false,
      proposal,
    }).reason,
    'proposal-stale',
  )
})

test('invoke requires exact family operation, healthy active generation and no ambiguity', () => {
  assert.equal(
    p7ComponentInvocationEligible({
      family: 'mcp',
      operation: 'mcp.call',
      state: 'active',
      health: 'healthy',
      bindingGeneration: 2,
      revoked: false,
      reconciliationRequired: false,
    }),
    true,
  )
  assert.equal(
    p7ComponentInvocationEligible({
      family: 'mcp',
      operation: 'sandbox.run',
      state: 'active',
      health: 'healthy',
      bindingGeneration: 2,
      revoked: false,
      reconciliationRequired: false,
    }),
    false,
  )
  assert.equal(
    p7ComponentInvocationEligible({
      family: 'mcp',
      operation: 'mcp.call',
      state: 'active',
      health: 'healthy',
      bindingGeneration: 2,
      revoked: false,
      reconciliationRequired: true,
    }),
    false,
  )
})

test('pending and unknown effects need reconciliation and emergency stop is scope-bound', () => {
  assert.equal(p7ComponentEffectNeedsReconciliation('pending'), true)
  assert.equal(p7ComponentEffectNeedsReconciliation('unknown'), true)
  assert.equal(p7ComponentEffectNeedsReconciliation('failed'), false)
  assert.equal(
    p7EmergencyStopEligible({
      viewWorkspaceId: WORKSPACE_A,
      snapshotWorkspaceId: WORKSPACE_A,
      activeOperationCount: 1,
      managedComponentCount: 0,
      stopInFlight: false,
    }),
    true,
  )
  assert.equal(
    p7EmergencyStopEligible({
      viewWorkspaceId: WORKSPACE_B,
      snapshotWorkspaceId: WORKSPACE_A,
      activeOperationCount: 1,
      managedComponentCount: 0,
      stopInFlight: false,
    }),
    false,
  )
  assert.equal(
    p7EmergencyStopEligible({
      viewWorkspaceId: WORKSPACE_A,
      snapshotWorkspaceId: WORKSPACE_A,
      activeOperationCount: 0,
      managedComponentCount: 1,
      stopInFlight: false,
    }),
    true,
  )
})

const ebookCatalog = {
  component_id: 'knowledge.ebook',
  component_version: '1.0.0',
  schema_version: 1,
  source_snapshot_sha256: 'b'.repeat(64),
  documents: [
    {
      id: 'document:1',
      title: 'OmniBase',
      type: 'guide',
      summary: '真实摘要',
      content: '真实内容',
      file_hash: 'c'.repeat(64),
      sections: [
        {
          id: 'section:1',
          heading: '边界',
          level: 2,
          position: 1,
          theme: 'security',
          content: '边界内容',
          explanation: '边界说明',
        },
      ],
    },
  ],
  glossary: [],
  invariants: [],
  modules: [],
}

const canvasOutput = {
  adapter: 'builtin-ui.v1',
  component_id: 'builtin.workspace-canvas',
  schema_version: 1,
  renderer: 'host_declarative',
  slot_id: 'editor.component',
  view_id: 'builtin.workspace-canvas',
  view: {
    kind: 'workspace_component_overview',
    title: 'Workspace Canvas',
    sections: [
      { kind: 'status', label: 'Runtime authority', value: 'Workspace-scoped' },
      { kind: 'status', label: 'Renderer posture', value: 'Host declarative' },
    ],
  },
} as const

function committedUiSnapshot(
  items: readonly Readonly<{
    componentId: string
    slotId: 'editor.component' | 'sidebar.component' | 'settings.component' | 'status.component'
    bindingKey: string
    orderIndex: number
    bindingGeneration?: number
    state?: 'active' | 'disabled' | 'revoked' | 'uninstalled'
    health?: 'healthy' | 'degraded' | 'unknown' | 'unavailable'
  }>[],
  workspaceId = WORKSPACE_A,
): DesktopWorkspaceComponentSnapshot {
  return {
    workspaceId,
    catalog: items.map((item, index) => ({
      componentId: item.componentId,
      version: '1.0.0',
      family: 'declarative_ui',
      displayName: item.componentId,
      publisherClass: 'owner_reviewed',
      adapterId: 'builtin-ui.v1',
      policyManifestSha256: String((index + 1) % 10).repeat(64),
      manifestSha256: 'a'.repeat(64),
      packageSha256: 'b'.repeat(64),
      operations: ['ui.render'],
      permissions: [],
      slots: [
        {
          slotId: item.slotId,
          cardinality: 'many',
          minimumOrder: 0,
          maximumOrder: 100,
          installationRevision: 1,
        },
      ],
      dependencies: [],
      conflicts: [],
      budgets: {
        maxCalls: 10,
        maxBytesIn: 0,
        maxBytesOut: 4096,
        maxTokens: 0,
        maxWallTimeMs: 5000,
        maxCostUnits: 1,
        maxRetries: 0,
        maxConcurrency: 4,
      },
      network: { required: false, serviceClasses: [] },
      recovery: {
        autoReplayUnknown: false,
        retention: 'retain_workspace_data',
        safeMode: 'disable_component',
      },
      stateSchema: { kind: 'canonical_json', version: 1 },
      settingsSchema: {
        kind: 'closed_object',
        version: 1,
        additionalProperties: false,
        properties: {},
        required: [],
      },
      available: true,
      unavailableReason: null,
    })),
    installations: items.map((item, index) => ({
      installationId: `installation_${String(index + 1).repeat(32)}`,
      workspaceId,
      componentId: item.componentId,
      version: '1.0.0',
      manifestSha256: 'a'.repeat(64),
      packageSha256: 'b'.repeat(64),
      state: item.state ?? 'active',
      revision: 1,
      bindingGeneration: item.bindingGeneration ?? 1,
      desiredConfiguration: {},
      currentSlotBindings: [
        {
          slotId: item.slotId,
          bindingKey: item.bindingKey,
          orderIndex: item.orderIndex,
          configuration: {},
        },
      ],
      dependencyGraph: [],
      health: item.health ?? 'healthy',
      lastErrorCode: null,
      updatedAt: '2026-08-30T00:00:00.000Z',
    })),
    proposals: [],
    operations: [],
    effects: [],
    grants: [],
    revocations: [],
    recoveries: [],
    reconciliations: [],
    audit: [],
  }
}

test('host canvas parser accepts only the typed editor Slot and exact closed output', () => {
  const input = {
    workspaceId: WORKSPACE_A,
    componentId: 'builtin.workspace-canvas',
    operationId: OPERATION_ID,
    operation: 'ui.render' as const,
    output: canvasOutput,
  }
  assert.deepEqual(p7ParseWorkspaceComponentSurface(input), {
    kind: 'workspace-canvas',
    workspaceId: WORKSPACE_A,
    componentId: 'builtin.workspace-canvas',
    operationId: OPERATION_ID,
    slotId: 'editor.component',
    viewId: 'builtin.workspace-canvas',
    renderer: 'host_declarative',
    title: 'Workspace Canvas',
    sections: [
      { kind: 'status', label: 'Runtime authority', value: 'Workspace-scoped' },
      { kind: 'status', label: 'Renderer posture', value: 'Host declarative' },
    ],
  })
  assert.equal(
    p7ParseWorkspaceComponentSurface({
      ...input,
      output: { ...input.output, slot_id: 'terminal.component' },
    }),
    null,
  )
  assert.equal(
    p7ParseWorkspaceComponentSurface({
      ...input,
      output: {
        ...input.output,
        view: {
          ...input.output.view,
          sections: [
            ...input.output.view.sections,
            { kind: 'status', label: 'Runtime authority', value: 'duplicate' },
          ],
        },
      },
    }),
    null,
  )
  assert.equal(
    p7ParseWorkspaceComponentSurface({
      ...input,
      output: { ...input.output, html: '<script>alert(1)</script>' },
    }),
    null,
  )
})

test('Owner declarative UI reaches a host surface while component identity drift is rejected', () => {
  const componentId = 'owner.focus-board'
  const input = {
    workspaceId: WORKSPACE_A,
    componentId,
    operationId: OPERATION_ID,
    operation: 'ui.render' as const,
    output: {
      ...canvasOutput,
      component_id: componentId,
      view_id: componentId,
      slot_id: 'sidebar.component' as const,
      view: { ...canvasOutput.view, title: 'Focus Board' },
    },
  }
  assert.deepEqual(p7ParseWorkspaceComponentSurface(input), {
    kind: 'workspace-canvas',
    workspaceId: WORKSPACE_A,
    componentId,
    operationId: OPERATION_ID,
    slotId: 'sidebar.component',
    viewId: componentId,
    renderer: 'host_declarative',
    title: 'Focus Board',
    sections: canvasOutput.view.sections,
  })
  assert.equal(
    p7ParseWorkspaceComponentSurface({
      ...input,
      output: { ...input.output, component_id: 'owner.other-board' },
    }),
    null,
  )
  assert.equal(
    p7ParseWorkspaceComponentSurface({
      ...input,
      output: { ...input.output, view_id: 'owner.other-board' },
    }),
    null,
  )
  assert.equal(
    p7ParseWorkspaceComponentSurface({ ...input, componentId: '../physical/package/path' }),
    null,
  )
})

test('declarative UI Slots project into exactly one host-owned region', () => {
  const componentId = 'owner.focus-board'
  for (const [slotId, expectedRegion] of [
    ['editor.component', 'editor'],
    ['sidebar.component', 'sidebar'],
    ['settings.component', 'settings'],
    ['status.component', 'status'],
  ] as const) {
    assert.equal(p7WorkspaceComponentHostSlotId(slotId), true)
    const state = p7SetWorkspaceComponentSurface(
      createP7WorkspaceComponentSurfaceState(WORKSPACE_A),
      {
        workspaceId: WORKSPACE_A,
        componentId,
        operationId: OPERATION_ID,
        operation: 'ui.render',
        state: 'succeeded',
        output: {
          ...canvasOutput,
          component_id: componentId,
          view_id: componentId,
          slot_id: slotId,
        },
      },
    )
    const projection = p7WorkspaceComponentSurfaceProjection({
      state,
      viewWorkspaceId: WORKSPACE_A,
      activeComponentIds: [componentId],
    })
    for (const region of ['editor', 'sidebar', 'settings', 'status'] as const) {
      const host = p7WorkspaceComponentHostProjection(projection, region)
      assert.equal(host.status, region === expectedRegion ? 'ready' : 'idle')
      assert.equal(
        host.surface?.componentId ?? null,
        region === expectedRegion ? componentId : null,
      )
    }
  }
  assert.equal(p7WorkspaceComponentHostSlotId('terminal.component'), false)
})

test('ready snapshot reconstructs every exact committed declarative UI binding', () => {
  const snapshot = committedUiSnapshot([
    {
      componentId: 'owner.editor-board',
      slotId: 'editor.component',
      bindingKey: 'primary',
      orderIndex: 20,
    },
    {
      componentId: 'owner.sidebar-board',
      slotId: 'sidebar.component',
      bindingKey: 'secondary',
      orderIndex: 5,
    },
  ])
  const bindings = p7WorkspaceComponentCommittedUiBindings(snapshot)
  assert.deepEqual(
    bindings.map((binding) => [binding.componentId, binding.slotId, binding.bindingKey]),
    [
      ['owner.editor-board', 'editor.component', 'primary'],
      ['owner.sidebar-board', 'sidebar.component', 'secondary'],
    ],
  )
  const empty = createP7WorkspaceComponentSurfaceState(WORKSPACE_A)
  assert.deepEqual(
    p7WorkspaceComponentSurfaceRequests(empty, snapshot).map((binding) => binding.key),
    bindings.map((binding) => binding.key),
  )

  let state = empty
  for (const binding of bindings) {
    state = p7SetWorkspaceComponentSurface(state, {
      workspaceId: WORKSPACE_A,
      componentId: binding.componentId,
      operationId: `${OPERATION_ID}_${binding.bindingKey}`,
      operation: 'ui.render',
      state: 'succeeded',
      output: {
        ...canvasOutput,
        component_id: binding.componentId,
        view_id: binding.componentId,
        slot_id: binding.slotId,
      },
      bindingGeneration: binding.bindingGeneration,
      slotId: binding.slotId,
      bindingKey: binding.bindingKey,
      orderIndex: binding.orderIndex,
    })
  }
  assert.deepEqual(p7WorkspaceComponentSurfaceRequests(state, snapshot), [])
  const projection = p7WorkspaceComponentSurfaceProjection({
    state,
    viewWorkspaceId: WORKSPACE_A,
    activeComponentIds: snapshot.installations.map((item) => item.componentId),
  })
  assert.equal(p7WorkspaceComponentHostProjection(projection, 'editor').surfaces.length, 1)
  assert.equal(p7WorkspaceComponentHostProjection(projection, 'sidebar').surfaces.length, 1)
})

test('many-cardinality Slot ordering is stable and one failed entry preserves siblings', () => {
  const items = [
    {
      componentId: 'owner.z-board',
      slotId: 'sidebar.component' as const,
      bindingKey: 'z',
      orderIndex: 10,
    },
    {
      componentId: 'owner.b-board',
      slotId: 'sidebar.component' as const,
      bindingKey: 'b',
      orderIndex: 5,
    },
    {
      componentId: 'owner.a-board',
      slotId: 'sidebar.component' as const,
      bindingKey: 'a',
      orderIndex: 5,
    },
  ]
  const snapshot = committedUiSnapshot(items)
  const bindings = p7WorkspaceComponentCommittedUiBindings(snapshot)
  let state = createP7WorkspaceComponentSurfaceState(WORKSPACE_A)
  for (const binding of bindings.toReversed()) {
    state = p7SetWorkspaceComponentSurface(state, {
      workspaceId: WORKSPACE_A,
      componentId: binding.componentId,
      operationId: `${OPERATION_ID}_${binding.bindingKey}`,
      operation: 'ui.render',
      state: binding.componentId === 'owner.b-board' ? 'failed' : 'succeeded',
      output:
        binding.componentId === 'owner.b-board'
          ? null
          : {
              ...canvasOutput,
              component_id: binding.componentId,
              view_id: binding.componentId,
              slot_id: binding.slotId,
            },
      bindingGeneration: binding.bindingGeneration,
      slotId: binding.slotId,
      bindingKey: binding.bindingKey,
      orderIndex: binding.orderIndex,
    })
  }
  const projection = p7WorkspaceComponentSurfaceProjection({
    state,
    viewWorkspaceId: WORKSPACE_A,
    activeComponentIds: items.map((item) => item.componentId),
  })
  assert.deepEqual(
    projection.entries.map((entry) => entry.componentId),
    ['owner.a-board', 'owner.b-board', 'owner.z-board'],
  )
  assert.deepEqual(
    projection.surfaces.map((surface) => surface.componentId),
    ['owner.a-board', 'owner.z-board'],
  )
  assert.equal(projection.failures[0]?.componentId, 'owner.b-board')
  assert.equal(projection.failures[0]?.safeModeReason, 'invocation-failed')
})

test('generation and lifecycle drift invalidate only stale entries and request replacements', () => {
  const initialSnapshot = committedUiSnapshot([
    {
      componentId: 'owner.keep-board',
      slotId: 'editor.component',
      bindingKey: 'keep',
      orderIndex: 0,
    },
    {
      componentId: 'owner.change-board',
      slotId: 'sidebar.component',
      bindingKey: 'old',
      orderIndex: 0,
    },
  ])
  let state = createP7WorkspaceComponentSurfaceState(WORKSPACE_A)
  for (const binding of p7WorkspaceComponentCommittedUiBindings(initialSnapshot)) {
    state = p7SetWorkspaceComponentSurface(state, {
      workspaceId: WORKSPACE_A,
      componentId: binding.componentId,
      operationId: `${OPERATION_ID}_${binding.bindingKey}`,
      operation: 'ui.render',
      state: 'succeeded',
      output: {
        ...canvasOutput,
        component_id: binding.componentId,
        view_id: binding.componentId,
        slot_id: binding.slotId,
      },
      bindingGeneration: binding.bindingGeneration,
      slotId: binding.slotId,
      bindingKey: binding.bindingKey,
      orderIndex: binding.orderIndex,
    })
  }
  const nextSnapshot = committedUiSnapshot([
    {
      componentId: 'owner.keep-board',
      slotId: 'editor.component',
      bindingKey: 'keep',
      orderIndex: 0,
    },
    {
      componentId: 'owner.change-board',
      slotId: 'sidebar.component',
      bindingKey: 'new',
      orderIndex: 1,
      bindingGeneration: 2,
    },
    {
      componentId: 'owner.disabled-board',
      slotId: 'status.component',
      bindingKey: 'disabled',
      orderIndex: 0,
      state: 'disabled',
    },
  ])
  const committed = p7WorkspaceComponentCommittedUiBindings(nextSnapshot)
  const reconciled = p7ReconcileWorkspaceComponentSurfaces(state, WORKSPACE_A, committed)
  assert.deepEqual(
    reconciled.entries.map((entry) => entry.componentId),
    ['owner.keep-board'],
  )
  assert.deepEqual(
    p7WorkspaceComponentSurfaceRequests(reconciled, nextSnapshot).map(
      (binding) => binding.componentId,
    ),
    ['owner.change-board'],
  )
})

test('host Slot projection rejects cross-Workspace and inactive component content', () => {
  const componentId = 'owner.focus-board'
  const state = p7SetWorkspaceComponentSurface(
    createP7WorkspaceComponentSurfaceState(WORKSPACE_A),
    {
      workspaceId: WORKSPACE_A,
      componentId,
      operationId: OPERATION_ID,
      operation: 'ui.render',
      state: 'succeeded',
      output: {
        ...canvasOutput,
        component_id: componentId,
        view_id: componentId,
        slot_id: 'status.component',
      },
    },
  )
  const parked = p7WorkspaceComponentSurfaceProjection({
    state,
    viewWorkspaceId: WORKSPACE_B,
    activeComponentIds: [componentId],
  })
  assert.equal(p7WorkspaceComponentHostProjection(parked, 'status').status, 'idle')

  const inactive = p7WorkspaceComponentSurfaceProjection({
    state,
    viewWorkspaceId: WORKSPACE_A,
    activeComponentIds: [],
  })
  assert.equal(p7WorkspaceComponentHostProjection(inactive, 'status').status, 'idle')
  assert.equal(p7WorkspaceComponentHostProjection(inactive, 'editor').status, 'safe-mode')
  assert.equal(
    p7WorkspaceComponentHostProjection(inactive, 'editor').safeModeReason,
    'component-inactive',
  )
})

test('knowledge ebook parser accepts the verified closed catalog and rejects drift', () => {
  const input = {
    workspaceId: WORKSPACE_A,
    componentId: 'knowledge.ebook',
    operationId: OPERATION_ID,
    operation: 'local_adapter.open' as const,
    output: {
      adapter: 'trusted-local-app.v1',
      asset_id: 'knowledge.ebook/1.0.0/catalog',
      asset_sha256: 'd'.repeat(64),
      component_manifest_sha256: 'e'.repeat(64),
      component_package_sha256: 'c'.repeat(64),
      catalog: ebookCatalog,
      destination: 'workspace',
      logical_id: null,
      renderer: 'host_declarative',
    },
  }
  const parsed = p7ParseWorkspaceComponentSurface(input)
  assert.equal(parsed?.kind, 'knowledge-ebook')
  assert.equal(
    parsed?.kind === 'knowledge-ebook' ? parsed.assetId : null,
    'knowledge.ebook/1.0.0/catalog',
  )
  assert.equal(
    parsed?.kind === 'knowledge-ebook' ? parsed.catalog.documents[0]?.title : null,
    'OmniBase',
  )
  assert.equal(parsed?.kind === 'knowledge-ebook' ? parsed.assetSha256 : null, 'd'.repeat(64))
  assert.equal(
    parsed?.kind === 'knowledge-ebook' ? parsed.componentPackageSha256 : null,
    'c'.repeat(64),
  )
  assert.equal(
    p7ParseWorkspaceComponentSurface({
      ...input,
      output: {
        ...input.output,
        catalog: { ...ebookCatalog, remote_url: 'https://example.invalid' },
      },
    }),
    null,
  )
  assert.equal(
    p7ParseWorkspaceComponentSurface({
      ...input,
      output: { ...input.output, asset_id: 'knowledge.ebook/2.0.0/catalog' },
    }),
    null,
  )
})

test('instruction skill output is strict, bounded and visible as a host-owned surface', () => {
  const input = {
    workspaceId: WORKSPACE_A,
    componentId: 'builtin.instruction-skill',
    operationId: OPERATION_ID,
    operation: 'skill.resolve' as const,
    output: {
      adapter: 'instruction-skill.v1',
      authority: 'instruction_only',
      component_id: 'builtin.instruction-skill',
      instructions: 'Return an Owner-reviewable proposal.',
      skill_id: 'builtin.instruction-skill',
      task_sha256: 'f'.repeat(64),
    },
  }
  const parsed = p7ParseWorkspaceComponentSurface(input)
  assert.equal(parsed?.kind, 'instruction-skill')
  assert.match(
    parsed === null ? '' : p7WorkspaceComponentResultEventLogLine(parsed),
    /instruction_only/,
  )
  assert.equal(
    p7ParseWorkspaceComponentSurface({
      ...input,
      output: { ...input.output, authority: 'tool_execution' },
    }),
    null,
  )
  assert.equal(
    p7ParseWorkspaceComponentSurface({
      ...input,
      output: { ...input.output, instructions: 'x'.repeat(32_769) },
    }),
    null,
  )
})

test('read-only MCP parser accepts each broker result and rejects extra or unbounded fields', () => {
  const base = {
    workspaceId: WORKSPACE_A,
    componentId: 'builtin.readonly-mcp',
    operationId: OPERATION_ID,
    operation: 'mcp.call' as const,
  }
  const list = p7ParseWorkspaceComponentSurface({
    ...base,
    output: {
      tool: 'omnibase_files_list',
      directory_path: 'src',
      entries: [{ kind: 'file', name: 'index.ts', path: 'src/index.ts', size_bytes: 42 }],
      truncated: false,
    },
  })
  assert.equal(list?.kind, 'readonly-mcp')
  assert.equal(list?.kind === 'readonly-mcp' ? list.result.kind : null, 'list')

  const read = p7ParseWorkspaceComponentSurface({
    ...base,
    output: {
      tool: 'omnibase_files_read',
      path: 'src/index.ts',
      content: 'export {}',
      size_bytes: 9,
      sha256: '1'.repeat(64),
    },
  })
  assert.equal(read?.kind === 'readonly-mcp' ? read.result.kind : null, 'read')

  const hash = p7ParseWorkspaceComponentSurface({
    ...base,
    output: {
      tool: 'omnibase_files_hash',
      path: 'src/index.ts',
      size_bytes: 9,
      sha256: '2'.repeat(64),
    },
  })
  assert.equal(hash?.kind === 'readonly-mcp' ? hash.result.kind : null, 'hash')

  const search = p7ParseWorkspaceComponentSurface({
    ...base,
    output: {
      tool: 'omnibase_text_search',
      path: 'src/index.ts',
      matches: [{ line: 1, snippet: 'export {}' }],
      truncated: false,
    },
  })
  assert.equal(search?.kind === 'readonly-mcp' ? search.result.kind : null, 'search')

  assert.equal(
    p7ParseWorkspaceComponentSurface({
      ...base,
      output: {
        tool: 'omnibase_files_read',
        path: 'src/index.ts',
        content: 'export {}',
        size_bytes: 9,
        sha256: '1'.repeat(64),
        physical_path: 'C:\\secret',
      },
    }),
    null,
  )
  assert.equal(
    p7ParseWorkspaceComponentSurface({
      ...base,
      output: {
        tool: 'omnibase_text_search',
        path: 'src/index.ts',
        matches: [{ line: 1, snippet: 'x'.repeat(513) }],
        truncated: false,
      },
    }),
    null,
  )
  assert.equal(
    p7ParseWorkspaceComponentSurface({
      ...base,
      output: {
        tool: 'omnibase_files_list',
        directory_path: 'src',
        entries: [
          { kind: 'file', name: 'nested/file.ts', path: 'src/nested/file.ts', size_bytes: 1 },
        ],
        truncated: false,
      },
    }),
    null,
  )
  assert.equal(
    p7ParseWorkspaceComponentSurface({
      ...base,
      output: {
        jsonrpc: '2.0',
        id: OPERATION_ID,
        result: {
          tool: 'omnibase_files_hash',
          path: 'src/index.ts',
          size_bytes: 9,
          sha256: '2'.repeat(64),
        },
      },
    }),
    null,
  )
})

test('sandbox output is an exact bounded host surface and rejects generic adapter JSON', () => {
  const input = {
    workspaceId: WORKSPACE_A,
    componentId: 'builtin.sandbox-workload',
    operationId: OPERATION_ID,
    operation: 'sandbox.run' as const,
    output: {
      adapter: 'p34-sandbox.v1',
      schema_version: 1,
      component_id: 'builtin.sandbox-workload',
      workload_id: 'bounded-transform',
      runtime_instance_id: `runtime_${'c'.repeat(32)}`,
      status: 'completed',
      input_artifact_ids: ['artifact.input.1'],
      result: {
        kind: 'artifact_inventory',
        artifact_count: 2,
        fingerprint_sha256: '3'.repeat(64),
      },
      usage: { bytes_in: 24, bytes_out: 96, wall_time_ms: 14 },
    },
  }
  const parsed = p7ParseWorkspaceComponentSurface(input)
  assert.equal(parsed?.kind, 'sandbox-workload')
  assert.match(
    parsed === null ? '' : p7WorkspaceComponentResultEventLogLine(parsed),
    /artifact_inventory/,
  )
  assert.equal(
    p7ParseWorkspaceComponentSurface({ ...input, output: { ...input.output, command: 'cmd.exe' } }),
    null,
  )
  assert.equal(
    p7ParseWorkspaceComponentSurface({
      ...input,
      output: { ...input.output, result: { ...input.output.result, kind: 'generic_json' } },
    }),
    null,
  )
})

test('component surface never projects across Workspaces on the first frame', () => {
  const state = p7SetWorkspaceComponentSurface(
    createP7WorkspaceComponentSurfaceState(WORKSPACE_A),
    {
      workspaceId: WORKSPACE_A,
      componentId: 'builtin.workspace-canvas',
      operationId: OPERATION_ID,
      operation: 'ui.render',
      state: 'succeeded',
      output: canvasOutput,
    },
  )
  const projection = p7WorkspaceComponentSurfaceProjection({
    state,
    viewWorkspaceId: WORKSPACE_B,
    activeComponentIds: ['builtin.workspace-canvas'],
  })
  assert.equal(projection.status, 'loading')
  assert.deepEqual(projection.entries, [])
})

test('malformed output and inactive components fail into standard-workbench safe mode', () => {
  const malformed = p7SetWorkspaceComponentSurface(
    createP7WorkspaceComponentSurfaceState(WORKSPACE_A),
    {
      workspaceId: WORKSPACE_A,
      componentId: 'builtin.workspace-canvas',
      operationId: OPERATION_ID,
      operation: 'ui.render',
      state: 'succeeded',
      output: { renderer: 'host_declarative' },
    },
  )
  const malformedProjection = p7WorkspaceComponentSurfaceProjection({
    state: malformed,
    viewWorkspaceId: WORKSPACE_A,
    activeComponentIds: ['builtin.workspace-canvas'],
  })
  assert.equal(malformedProjection.status, 'ready')
  assert.equal(malformedProjection.failures[0]?.safeModeReason, 'malformed-output')

  const ready = p7SetWorkspaceComponentSurface(
    createP7WorkspaceComponentSurfaceState(WORKSPACE_A),
    {
      workspaceId: WORKSPACE_A,
      componentId: 'builtin.workspace-canvas',
      operationId: OPERATION_ID,
      operation: 'ui.render',
      state: 'succeeded',
      output: canvasOutput,
    },
  )
  assert.equal(
    p7WorkspaceComponentSurfaceProjection({
      state: ready,
      viewWorkspaceId: WORKSPACE_A,
      activeComponentIds: [],
    }).safeModeReason,
    'component-inactive',
  )
})

test('emergency stop clears every non-core surface without changing Workspace identity', () => {
  const stopped = p7EnterWorkspaceComponentSafeMode(WORKSPACE_A, 'emergency-stop')
  const projection = p7WorkspaceComponentSurfaceProjection({
    state: stopped,
    viewWorkspaceId: WORKSPACE_A,
    activeComponentIds: [],
  })
  assert.equal(projection.status, 'safe-mode')
  assert.equal(projection.safeModeReason, 'emergency-stop')
  assert.deepEqual(projection.entries, [])
})
