export interface P7VisualBridgeOptions {
  readonly reduceMotion?: boolean
}

export function installP7VisualDesktopBridge(options: P7VisualBridgeOptions = {}) {
  const now = '2026-08-30T00:00:00.000Z'
  const ownerId = `owner_${'1'.repeat(32)}`
  const workspaceId = `workspace_${'2'.repeat(32)}`
  const sha = (value: string) => value.repeat(64).slice(0, 64)
  const ok = <T>(value: T) => ({ ok: true as const, value })
  const unavailable = async () => ({
    ok: false as const,
    error: { code: 'desktop_visual_gate_read_only' },
  })

  const owner = {
    id: ownerId,
    displayName: 'P7.4 Visual Owner',
    createdAt: now,
    updatedAt: now,
  }
  const workspace = {
    id: workspaceId,
    ownerId,
    name: 'P7.4 Workspace with a deliberately complete identity',
    state: 'active' as const,
    rowVersion: 1,
    createdAt: now,
    updatedAt: now,
  }
  const slotIds = [
    'agent.rail',
    'conversation.transcript',
    'event.agent-log',
    'event.output',
    'knowledge.ebook',
    'mcp.catalog',
    'provider.settings',
    'run.history',
    'sandbox.runtime',
    'settings.center',
    'skills.catalog',
    'source-control',
    'terminal',
    'workspace.brief',
    'workspace.explorer',
  ] as const
  const profileValue = {
    schemaVersion: 1 as const,
    template: { id: 'standard-workbench' as const, version: 1 as const },
    appearance: { density: 'inherit' as const, quietChrome: false },
    layout: {
      agentPanel: 'open' as const,
      bottomPanel: 'output' as const,
      focusMode: false,
      sidebar: 'explorer' as const,
    },
    slots: Object.fromEntries(
      slotIds.map((slotId) => [slotId, !['source-control', 'terminal'].includes(slotId)]),
    ),
  }
  const composition = {
    profile: {
      workspaceId,
      revision: 1,
      profileSha256: sha('a'),
      sourceKind: 'system' as const,
      proposalId: null,
      value: profileValue,
      createdAt: now,
    },
    revisions: [],
    proposals: [],
    slotCatalog: slotIds.map((id) => ({
      id,
      label: id,
      region: id === 'agent.rail' ? ('right' as const) : ('settings' as const),
      posture: ['source-control', 'terminal'].includes(id)
        ? ('unavailable' as const)
        : id === 'settings.center'
          ? ('required' as const)
          : ('admitted' as const),
    })),
    audit: [],
  }

  const families = [
    {
      family: 'declarative_ui' as const,
      adapterId: 'builtin-ui.v1' as const,
      operation: 'ui.render' as const,
      componentId: 'builtin.workspace-canvas',
      displayName: 'Workspace Canvas',
      slotId: 'editor.component',
    },
    {
      family: 'instruction_skill' as const,
      adapterId: 'instruction-skill.v1' as const,
      operation: 'skill.resolve' as const,
      componentId: 'builtin.workspace-brief-skill',
      displayName: 'Workspace Brief Instruction Skill',
      slotId: 'settings.component',
    },
    {
      family: 'mcp_connector' as const,
      adapterId: 'readonly-mcp.v1' as const,
      operation: 'mcp.call' as const,
      componentId: 'builtin.knowledge-ebook-mcp',
      displayName: 'Knowledge Ebook Read-only MCP',
      slotId: 'settings.component',
    },
    {
      family: 'sandbox_workload' as const,
      adapterId: 'p34-sandbox.v1' as const,
      operation: 'sandbox.run' as const,
      componentId: 'builtin.zero-import-transform',
      displayName: 'Exact Package Zero-import Sandbox Workload',
      slotId: 'settings.component',
    },
    {
      family: 'trusted_local_adapter' as const,
      adapterId: 'trusted-local-app.v1' as const,
      operation: 'local_adapter.open' as const,
      componentId: 'builtin.workspace-files-adapter',
      displayName: 'Source-owned Workspace Files Adapter',
      slotId: 'sidebar.component',
    },
  ]
  const catalog = families.flatMap((family, familyIndex) =>
    ['1.0.0', '1.1.0'].map((version, versionIndex) => ({
      componentId: family.componentId,
      version,
      family: family.family,
      displayName: `${family.displayName} ${version}`,
      publisherClass: 'source_owned' as const,
      adapterId: family.adapterId,
      policyManifestSha256: sha(String(familyIndex + 1)),
      manifestSha256: sha(String(familyIndex + 2)),
      packageSha256: sha(String(familyIndex + 3)),
      operations: [family.operation],
      permissions: [
        {
          action: family.operation,
          dataScope:
            family.family === 'sandbox_workload'
              ? ('none' as const)
              : ('workspace_logical' as const),
          logicalResourceClasses:
            family.family === 'sandbox_workload' ? [] : ['workspace.component.input'],
          secretReferenceClasses: [],
        },
      ],
      slots: [
        {
          slotId: family.slotId,
          cardinality: 'many' as const,
          minimumOrder: 0,
          maximumOrder: 100,
        },
      ],
      dependencies: [],
      conflicts: [],
      budgets: {
        maxCalls: 64,
        maxBytesIn: 1_048_576,
        maxBytesOut: 4_194_304,
        maxTokens: 131_072,
        maxWallTimeMs: 600_000,
        maxCostUnits: 1_000,
        maxRetries: 0,
        maxConcurrency: 1,
      },
      network: { required: false, serviceClasses: [] },
      recovery: {
        autoReplayUnknown: false as const,
        retention: 'retain_workspace_data' as const,
        safeMode: 'disable_component' as const,
      },
      stateSchema: { kind: 'canonical_json' as const, version: 1 },
      settingsSchema: {
        kind: 'closed_object' as const,
        version: 1,
        additionalProperties: false as const,
        properties: {
          label: { type: 'string' as const, default: 'Workspace component', maxLength: 80 },
          enabled: { type: 'boolean' as const, default: true },
        },
        required: ['label'],
      },
      available: versionIndex === 0,
      unavailableReason: versionIndex === 0 ? null : ('package_not_attested' as const),
    })),
  )
  const installations = families.map((family, index) => ({
    installationId: `installation_${String(index + 1).repeat(32)}`,
    workspaceId,
    componentId: family.componentId,
    version: '1.0.0',
    manifestSha256: sha(String(index + 2)),
    packageSha256: sha(String(index + 3)),
    state: index === 4 ? ('disabled' as const) : ('active' as const),
    revision: 1,
    bindingGeneration: index + 1,
    desiredConfiguration: { label: family.displayName, enabled: true },
    currentSlotBindings: [
      {
        slotId: family.slotId,
        bindingKey: `visual-${index + 1}`,
        orderIndex: index,
        configuration: {},
      },
    ],
    dependencyGraph: [],
    health: index === 4 ? ('unavailable' as const) : ('healthy' as const),
    lastErrorCode: index === 4 ? 'component_disabled_by_owner' : null,
    updatedAt: now,
  }))
  const componentSnapshot = {
    workspaceId,
    catalog,
    installations,
    proposals: [],
    operations: [],
    effects: [],
    grants: [],
    revocations: [],
    recoveries: [],
    reconciliations: [],
    audit: [],
  }

  const bridge = {
    app: { getVersion: async () => '1.0.0-p7.4-visual-gate' },
    runtime: {
      getStatus: async () => ({ phase: 'ready' as const, attempts: 1, lastError: null }),
      retryStartup: async () => ({ phase: 'ready' as const, attempts: 1, lastError: null }),
    },
    owner: {
      getStatus: async () => ok({ initialized: true as const, owner }),
      bootstrap: unavailable,
    },
    workspaces: {
      list: async () => ok({ items: [workspace] }),
      create: unavailable,
      archive: unavailable,
      agent: async () =>
        ok({
          agent: {
            id: `agent_${'3'.repeat(32)}`,
            workspaceId,
            role: 'parent' as const,
            displayName: 'OMNIA',
            createdAt: now,
            updatedAt: now,
          },
        }),
    },
    workbenchSettings: {
      get: async () =>
        ok({
          preference: {
            density: 'compact' as const,
            reduceMotion: options.reduceMotion ?? false,
            rowVersion: 1,
            updatedAt: now,
          },
        }),
      update: unavailable,
    },
    workspaceComposition: {
      get: async () => ok(composition),
      propose: unavailable,
      proposeFromAssistant: unavailable,
      proposeRollback: unavailable,
      decide: unavailable,
    },
    workspaceComponents: {
      get: async () => ok(componentSnapshot),
      propose: unavailable,
      proposeFromAssistant: unavailable,
      importOwnerPackage: unavailable,
      importAssistantPackage: unavailable,
      decide: unavailable,
      action: unavailable,
      invoke: unavailable,
      emergencyStop: unavailable,
      reconcile: unavailable,
    },
    workspaceFiles: {
      authorize: unavailable,
      release: unavailable,
      list: unavailable,
      read: unavailable,
    },
    providers: {
      list: async () => ok({ items: [] }),
      upsert: unavailable,
      delete: unavailable,
      test: unavailable,
    },
    conversations: {
      list: async () => ok({ items: [] }),
      create: unavailable,
      archive: unavailable,
      get: unavailable,
      send: unavailable,
      cancel: unavailable,
      abortInFlightSend: unavailable,
      subscribe: () => () => undefined,
    },
    agents: {
      roles: {
        list: async () => ok({ items: [] }),
        get: unavailable,
        update: unavailable,
        test: unavailable,
      },
    },
    teamRuns: {
      start: unavailable,
      cancel: unavailable,
      get: unavailable,
      list: async () => ok({ items: [] }),
      submitProposal: unavailable,
      getBlackboard: unavailable,
      recordCollaboration: unavailable,
      execute: unavailable,
      appendBudget: unavailable,
      subscribe: () => () => undefined,
    },
  }

  Object.defineProperty(window, 'omnibaseDesktop', {
    configurable: false,
    enumerable: false,
    value: bridge,
    writable: false,
  })
}
