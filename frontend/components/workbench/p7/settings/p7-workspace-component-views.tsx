'use client'

import {
  Activity,
  Ban,
  Bot,
  Boxes,
  Braces,
  Check,
  CircleStop,
  FileKey,
  History,
  PackagePlus,
  Play,
  RefreshCw,
  ShieldAlert,
  ShieldCheck,
  Sparkles,
  Unplug,
  X,
} from 'lucide-react'
import { useState } from 'react'

import type {
  DesktopWorkspaceComponentCatalogItem,
  DesktopWorkspaceComponentEffect,
  DesktopWorkspaceComponentDependencyRequest,
  DesktopWorkspaceComponentGrantRequest,
  DesktopWorkspaceComponentInstallation,
  DesktopWorkspaceComponentJsonValue,
  DesktopWorkspaceComponentLifecycleAction,
  DesktopWorkspaceComponentOperation,
  DesktopWorkspaceComponentProposal,
  DesktopWorkspaceComponentSnapshot,
  DesktopWorkspaceComponentSlotBindingRequest,
} from '@/lib/desktop-bridge'
import {
  p7ComponentEffectNeedsReconciliation,
  p7DeclarativeSettingsDefaults,
  p7DeclarativeSettingsDiff,
  p7EmergencyStopEligible,
  p7ParseDeclarativeSettingsSchema,
  p7ValidateDeclarativeSettings,
  p7WorkspaceComponentHostSlotId,
  p7WorkspaceComponentLifecycleActions,
  type P7AssistantDeclarativePackageReview,
  type P7DeclarativeSettings,
  type P7DeclarativeSettingsSchema,
  type P7WorkspaceComponentsLoadStatus,
} from '@/lib/p7-workspace-components'
import {
  P7SettingRow,
  P7SettingsEmpty,
  P7SettingsSection,
  P7SettingsStatus,
  P7SettingsToggle,
} from './p7-settings-shared'

export type P7WorkspaceComponentSettingsSection =
  | 'catalog'
  | 'installed'
  | 'slots'
  | 'skills'
  | 'mcp'
  | 'sandbox'
  | 'local-adapters'
  | 'permissions'
  | 'health'
  | 'component-review'
  | 'component-audit'
  | 'recovery'

export interface P7WorkspaceComponentSettingsProps {
  readonly workspaceName: string
  readonly workspaceId: string | null
  readonly status: P7WorkspaceComponentsLoadStatus
  readonly snapshot: DesktopWorkspaceComponentSnapshot | null
  readonly busy: boolean
  readonly notice: string | null
  readonly assistantIntent: string
  readonly onAssistantIntentChange: (value: string) => void
  readonly assistantPackageReview: P7AssistantDeclarativePackageReview | null
  readonly onRequestAssistantPackage: () => void
  readonly onRegisterAssistantPackage: () => void
  readonly onDiscardAssistantPackage: () => void
  readonly onRequestAssistantProposal: () => void
  readonly onImportOwnerPackage: () => void
  readonly onPropose: (
    catalog: DesktopWorkspaceComponentCatalogItem,
    action: DesktopWorkspaceComponentLifecycleAction,
    draft: P7WorkspaceComponentProposalDraft,
  ) => void
  readonly onDecide: (
    proposal: DesktopWorkspaceComponentProposal,
    decision: 'approve' | 'reject',
  ) => void
  readonly onAction: (proposal: DesktopWorkspaceComponentProposal) => void
  readonly onInvoke: (
    installation: DesktopWorkspaceComponentInstallation,
    request: P7WorkspaceComponentInvokeRequest,
  ) => void
  readonly onEmergencyStop: () => void
  readonly onReconcile: (
    effect: DesktopWorkspaceComponentEffect,
    outcome: 'succeeded' | 'failed',
    evidenceSha256: string,
  ) => void
}

export type P7WorkspaceComponentInvokeRequest =
  | Readonly<{
      operation: 'ui.render'
      arguments: Readonly<{ slotId: string; viewId: string }>
    }>
  | Readonly<{
      operation: 'skill.resolve'
      arguments: Readonly<{ skillId: string; task: string }>
    }>
  | Readonly<{
      operation: 'mcp.call'
      arguments: Readonly<{
        toolName:
          | 'omnibase_files_list'
          | 'omnibase_files_read'
          | 'omnibase_files_hash'
          | 'omnibase_text_search'
        path?: string
        query?: string
      }>
    }>
  | Readonly<{
      operation: 'sandbox.run'
      arguments: Readonly<{ workloadId: string; inputArtifactIds: readonly string[] }>
    }>
  | Readonly<{
      operation: 'local_adapter.open'
      arguments: Readonly<{
        adapterId: 'knowledge.ebook'
        destination: 'workspace'
        logicalId?: string
      }>
    }>

export interface P7WorkspaceComponentProposalDraft {
  readonly requestedGrants: readonly DesktopWorkspaceComponentGrantRequest[]
  readonly desiredConfiguration: DesktopWorkspaceComponentJsonValue
  readonly desiredSlotBindings: readonly DesktopWorkspaceComponentSlotBindingRequest[]
  readonly dependencyGraph: readonly DesktopWorkspaceComponentDependencyRequest[]
}

const FAMILY_LABEL = Object.freeze({
  declarative_ui: 'UI / Canvas',
  instruction_skill: 'Instruction Skill',
  mcp_connector: 'MCP',
  sandbox_workload: 'Sandbox',
  trusted_local_adapter: 'Local Adapter',
})

const ACTION_LABEL: Readonly<Record<DesktopWorkspaceComponentLifecycleAction, string>> =
  Object.freeze({
    install: '安装',
    bind: '绑定',
    activate: '启用',
    disable: '停用',
    upgrade: '升级',
    rollback: '回滚',
    revoke: '撤销',
    uninstall: '卸载',
  })

function catalogFor(
  snapshot: DesktopWorkspaceComponentSnapshot,
  componentId: string,
  version?: string,
): DesktopWorkspaceComponentCatalogItem | null {
  return (
    snapshot.catalog.find(
      (item) =>
        item.componentId === componentId && (version === undefined || item.version === version),
    ) ?? null
  )
}

function installationFor(
  snapshot: DesktopWorkspaceComponentSnapshot,
  componentId: string,
): DesktopWorkspaceComponentInstallation | null {
  return (
    snapshot.installations.find(
      (item) => item.componentId === componentId && item.state !== 'uninstalled',
    ) ?? null
  )
}

function proposalExecuted(
  snapshot: DesktopWorkspaceComponentSnapshot,
  proposal: DesktopWorkspaceComponentProposal,
): boolean {
  return snapshot.operations.some(
    (operation) =>
      operation.componentId === proposal.componentId &&
      operation.action === proposal.changeKind &&
      operation.requestSha256 === proposal.requestSha256,
  )
}

function statusTone(value: string): 'ready' | 'warning' | 'error' | 'muted' {
  if (value === 'active' || value === 'healthy' || value === 'succeeded') return 'ready'
  if (value === 'pending' || value === 'unknown' || value === 'degraded') return 'warning'
  if (value === 'failed' || value === 'blocked' || value === 'revoked' || value === 'unavailable')
    return 'error'
  return 'muted'
}

function LoadingState({ status }: { readonly status: P7WorkspaceComponentsLoadStatus }) {
  if (status === 'error') return <P7SettingsEmpty>组件控制面读取失败。</P7SettingsEmpty>
  if (status === 'idle') return <P7SettingsEmpty>请选择 Workspace。</P7SettingsEmpty>
  return <P7SettingsEmpty>正在读取 Workspace 组件…</P7SettingsEmpty>
}

function Digest({ children }: { readonly children: string }) {
  return (
    <code className="p7-component-digest" title={children}>
      {children}
    </code>
  )
}

function declarativeSettings(value: DesktopWorkspaceComponentJsonValue): P7DeclarativeSettings {
  if (value === null || typeof value !== 'object' || Array.isArray(value)) return Object.freeze({})
  const result: Record<string, string | number | boolean | null> = {}
  for (const [key, item] of Object.entries(value)) {
    if (
      item === null ||
      typeof item === 'string' ||
      typeof item === 'number' ||
      typeof item === 'boolean'
    ) {
      result[key] = item
    }
  }
  return Object.freeze(result)
}

function ComponentRow({
  item,
  trailing,
}: {
  readonly item: DesktopWorkspaceComponentCatalogItem
  readonly trailing: React.ReactNode
}) {
  return (
    <div className="p7-component-row">
      <span className={`p7-dot ${item.available ? 'p7-dot-green' : 'p7-dot-red'}`} />
      <div className="p7-component-identity">
        <strong>{item.displayName}</strong>
        <span>{item.componentId}</span>
      </div>
      <span>{FAMILY_LABEL[item.family]}</span>
      <span>{item.version}</span>
      <span>{item.available ? item.publisherClass : item.unavailableReason}</span>
      {trailing}
    </div>
  )
}

export function P7DeclarativeSettingsForm({
  rawSchema,
  value,
  disabled,
  onChange,
}: {
  readonly rawSchema: unknown
  readonly value: P7DeclarativeSettings
  readonly disabled: boolean
  readonly onChange: (value: P7DeclarativeSettings) => void
}) {
  const schema = p7ParseDeclarativeSettingsSchema(rawSchema)
  if (schema === null) return <P7SettingsEmpty>声明式设置 schema 不可用。</P7SettingsEmpty>
  return (
    <P7ValidatedDeclarativeSettingsForm
      schema={schema}
      value={value}
      disabled={disabled}
      onChange={onChange}
    />
  )
}

function P7ValidatedDeclarativeSettingsForm({
  schema,
  value,
  disabled,
  onChange,
}: {
  readonly schema: P7DeclarativeSettingsSchema
  readonly value: P7DeclarativeSettings
  readonly disabled: boolean
  readonly onChange: (value: P7DeclarativeSettings) => void
}) {
  const validation = p7ValidateDeclarativeSettings(schema, value)
  const patch = (id: string, next: string | number | boolean | null) =>
    onChange(Object.freeze({ ...value, [id]: next }))
  return (
    <div className="p7-declarative-settings" aria-invalid={!validation.valid}>
      {schema.sections.map((section) => (
        <div key={section.id} className="p7-settings-group">
          <h2 className="p7-settings-subtitle">{section.label}</h2>
          {section.fields.map((field) => {
            const current = value[field.id]
            return (
              <P7SettingRow
                key={field.id}
                label={field.label}
                meta={validation.errors[field.id] ?? field.description ?? undefined}
              >
                {field.control === 'boolean' ? (
                  <P7SettingsToggle
                    label={field.label}
                    checked={current === true}
                    disabled={disabled}
                    onChange={(checked) => patch(field.id, checked)}
                  />
                ) : field.control === 'select' ? (
                  <select
                    value={current === undefined || current === null ? '' : String(current)}
                    disabled={disabled}
                    onChange={(event) =>
                      patch(
                        field.id,
                        field.options.find((option) => String(option.value) === event.target.value)
                          ?.value ?? null,
                      )
                    }
                  >
                    <option value="" disabled={field.required}>
                      未选择
                    </option>
                    {field.options.map((option) => (
                      <option
                        key={`${typeof option.value}:${String(option.value)}`}
                        value={String(option.value)}
                      >
                        {option.label}
                      </option>
                    ))}
                  </select>
                ) : field.control === 'multiline' ? (
                  <textarea
                    value={typeof current === 'string' ? current : ''}
                    maxLength={field.maxLength ?? undefined}
                    disabled={disabled}
                    onChange={(event) => patch(field.id, event.target.value)}
                  />
                ) : (
                  <input
                    type={
                      field.control === 'integer' || field.control === 'number' ? 'number' : 'text'
                    }
                    value={
                      typeof current === 'string' || typeof current === 'number' ? current : ''
                    }
                    min={field.minimum ?? undefined}
                    max={field.maximum ?? undefined}
                    step={field.step ?? undefined}
                    maxLength={field.maxLength ?? undefined}
                    disabled={disabled}
                    autoComplete="off"
                    onChange={(event) =>
                      patch(
                        field.id,
                        field.control === 'integer' || field.control === 'number'
                          ? event.target.value === ''
                            ? null
                            : Number(event.target.value)
                          : event.target.value,
                      )
                    }
                  />
                )}
              </P7SettingRow>
            )
          })}
        </div>
      ))}
    </div>
  )
}

function defaultGrant(
  catalog: DesktopWorkspaceComponentCatalogItem,
  operation: DesktopWorkspaceComponentOperation,
): DesktopWorkspaceComponentGrantRequest {
  return Object.freeze({
    action: operation,
    logicalResourceId: null,
    resourceVersion: null,
    logicalServiceId: null,
    expiresInSeconds: 3_600,
    maximumInvocations: catalog.budgets.maxCalls,
    maximumBytesIn: catalog.budgets.maxBytesIn,
    maximumBytesOut: catalog.budgets.maxBytesOut,
    maximumTokens: catalog.budgets.maxTokens,
    maximumWallTimeMs: catalog.budgets.maxWallTimeMs,
    maximumCostUnits: catalog.budgets.maxCostUnits,
  })
}

function initialDependencyGraph(
  catalog: DesktopWorkspaceComponentCatalogItem,
): readonly DesktopWorkspaceComponentDependencyRequest[] {
  return Object.freeze(catalog.dependencies.map((dependency) => Object.freeze({ ...dependency })))
}

function dependencyInstalled(
  snapshot: DesktopWorkspaceComponentSnapshot,
  dependency: DesktopWorkspaceComponentDependencyRequest,
): boolean {
  return snapshot.installations.some(
    (installation) =>
      installation.componentId === dependency.componentId &&
      installation.version === dependency.version &&
      installation.manifestSha256 === dependency.manifestSha256 &&
      installation.packageSha256 === dependency.packageSha256 &&
      installation.state !== 'revoked' &&
      installation.state !== 'uninstalled',
  )
}

function P7ComponentProposalComposer({
  snapshot,
  catalog,
  action,
  busy,
  onCancel,
  onSubmit,
}: {
  readonly snapshot: DesktopWorkspaceComponentSnapshot
  readonly catalog: DesktopWorkspaceComponentCatalogItem
  readonly action: DesktopWorkspaceComponentLifecycleAction
  readonly busy: boolean
  readonly onCancel: () => void
  readonly onSubmit: (draft: P7WorkspaceComponentProposalDraft) => void
}) {
  const schema = p7ParseDeclarativeSettingsSchema(catalog.settingsSchema)
  const installation = installationFor(snapshot, catalog.componentId)
  const preservesCurrentBinding =
    installation !== null && installation.version === catalog.version && action !== 'install'
  const [configuration, setConfiguration] = useState<P7DeclarativeSettings>(() =>
    preservesCurrentBinding
      ? declarativeSettings(installation.desiredConfiguration)
      : schema === null
        ? Object.freeze({})
        : p7DeclarativeSettingsDefaults(schema),
  )
  const [slotBindings, setSlotBindings] = useState<
    readonly DesktopWorkspaceComponentSlotBindingRequest[]
  >(() =>
    preservesCurrentBinding
      ? installation.currentSlotBindings
      : Object.freeze(
          catalog.slots.map((slot) =>
            Object.freeze({
              slotId: slot.slotId,
              bindingKey: `${catalog.componentId}.${slot.slotId}`,
              orderIndex: slot.minimumOrder,
              configuration: Object.freeze({}),
            }),
          ),
        ),
  )
  const [dependencyGraph] = useState<readonly DesktopWorkspaceComponentDependencyRequest[]>(() =>
    preservesCurrentBinding ? installation.dependencyGraph : initialDependencyGraph(catalog),
  )
  const [requestedGrants, setRequestedGrants] = useState<
    readonly DesktopWorkspaceComponentGrantRequest[]
  >(() => Object.freeze(catalog.operations.map((operation) => defaultGrant(catalog, operation))))

  const configurationValidation =
    schema === null
      ? { valid: false, errors: {} }
      : p7ValidateDeclarativeSettings(schema, configuration)
  const dependenciesComplete = catalog.dependencies.every((required) =>
    dependencyGraph.some(
      (dependency) =>
        dependency.componentId === required.componentId &&
        dependency.version === required.version &&
        dependency.policyManifestSha256 === required.policyManifestSha256 &&
        dependency.manifestSha256 === required.manifestSha256 &&
        dependency.packageSha256 === required.packageSha256 &&
        dependencyInstalled(snapshot, dependency),
    ),
  )
  const slotBindingsValid =
    slotBindings.every((binding) => {
      const slot = catalog.slots.find((item) => item.slotId === binding.slotId)
      return (
        slot !== undefined &&
        /^[a-z][a-z0-9_.:-]{1,127}$/u.test(binding.bindingKey) &&
        Number.isSafeInteger(binding.orderIndex) &&
        binding.orderIndex >= slot.minimumOrder &&
        binding.orderIndex <= slot.maximumOrder
      )
    }) && new Set(slotBindings.map((binding) => binding.bindingKey)).size === slotBindings.length
  const grantsValid =
    requestedGrants.length === catalog.operations.length &&
    new Set(requestedGrants.map((grant) => grant.action)).size === catalog.operations.length &&
    catalog.operations.every((operation) =>
      requestedGrants.some((grant) => grant.action === operation),
    ) &&
    requestedGrants.every(
      (grant) =>
        grant.expiresInSeconds >= 60 &&
        grant.maximumInvocations > 0 &&
        grant.maximumInvocations <= catalog.budgets.maxCalls &&
        grant.maximumBytesIn >= 0 &&
        grant.maximumBytesIn <= catalog.budgets.maxBytesIn &&
        grant.maximumBytesOut >= 0 &&
        grant.maximumBytesOut <= catalog.budgets.maxBytesOut &&
        grant.maximumTokens >= 0 &&
        grant.maximumTokens <= catalog.budgets.maxTokens &&
        grant.maximumWallTimeMs > 0 &&
        grant.maximumWallTimeMs <= catalog.budgets.maxWallTimeMs &&
        grant.maximumCostUnits > 0 &&
        grant.maximumCostUnits <= catalog.budgets.maxCostUnits &&
        (catalog.network.required
          ? grant.logicalServiceId !== null &&
            /^[a-z][a-z0-9_.:-]{1,127}$/u.test(grant.logicalServiceId)
          : grant.logicalServiceId === null) &&
        ((grant.logicalResourceId === null && grant.resourceVersion === null) ||
          (grant.logicalResourceId !== null &&
            /^[a-z][a-z0-9_.:-]{1,127}$/u.test(grant.logicalResourceId) &&
            grant.resourceVersion !== null &&
            Number.isSafeInteger(grant.resourceVersion) &&
            grant.resourceVersion > 0)),
    )
  const canSubmit =
    catalog.available &&
    configurationValidation.valid &&
    dependenciesComplete &&
    slotBindingsValid &&
    grantsValid

  const patchGrant = (actionId: string, patch: Partial<DesktopWorkspaceComponentGrantRequest>) => {
    setRequestedGrants((current) =>
      Object.freeze(
        current.map((grant) =>
          grant.action === actionId ? Object.freeze({ ...grant, ...patch }) : grant,
        ),
      ),
    )
  }

  return (
    <div className="p7-component-proposal-composer">
      <div className="p7-component-review-head">
        <div className="p7-component-identity">
          <strong>
            {ACTION_LABEL[action]} · {catalog.displayName}
          </strong>
          <span>Exact configuration / Slot / dependency / grant request</span>
        </div>
        <P7SettingsStatus tone={canSubmit ? 'ready' : 'warning'}>
          {canSubmit ? '可提交审阅' : '草案不完整'}
        </P7SettingsStatus>
      </div>

      <div className="p7-settings-group">
        <h2 className="p7-settings-subtitle">Configuration</h2>
        {schema === null || schema.sections.length === 0 ? (
          <P7SettingsEmpty>
            {schema?.sections.length === 0
              ? '此组件声明空配置。'
              : '配置 schema 未通过宿主闭合集校验；不能创建提案。'}
          </P7SettingsEmpty>
        ) : (
          <P7ValidatedDeclarativeSettingsForm
            schema={schema}
            value={configuration}
            disabled={busy}
            onChange={setConfiguration}
          />
        )}
      </div>

      <div className="p7-settings-group">
        <h2 className="p7-settings-subtitle">Slot Bindings</h2>
        {catalog.slots.map((slot) => {
          const binding = slotBindings.find((item) => item.slotId === slot.slotId)
          return (
            <div key={slot.slotId} className="p7-component-draft-row">
              <P7SettingsToggle
                label={slot.slotId}
                checked={binding !== undefined}
                disabled={busy}
                onChange={(enabled) =>
                  setSlotBindings((current) =>
                    enabled
                      ? Object.freeze([
                          ...current,
                          Object.freeze({
                            slotId: slot.slotId,
                            bindingKey: `${catalog.componentId}.${slot.slotId}`,
                            orderIndex: slot.minimumOrder,
                            configuration: Object.freeze({}),
                          }),
                        ])
                      : Object.freeze(current.filter((item) => item.slotId !== slot.slotId)),
                  )
                }
              />
              <strong>{slot.slotId}</strong>
              <input
                value={binding?.bindingKey ?? ''}
                disabled={busy || binding === undefined}
                aria-label={`${slot.slotId} binding key`}
                onChange={(event) =>
                  setSlotBindings((current) =>
                    Object.freeze(
                      current.map((item) =>
                        item.slotId === slot.slotId
                          ? Object.freeze({ ...item, bindingKey: event.target.value })
                          : item,
                      ),
                    ),
                  )
                }
              />
              <input
                type="number"
                min={slot.minimumOrder}
                max={slot.maximumOrder}
                value={binding?.orderIndex ?? 0}
                disabled={busy || binding === undefined}
                aria-label={`${slot.slotId} order`}
                onChange={(event) =>
                  setSlotBindings((current) =>
                    Object.freeze(
                      current.map((item) =>
                        item.slotId === slot.slotId
                          ? Object.freeze({ ...item, orderIndex: Number(event.target.value) })
                          : item,
                      ),
                    ),
                  )
                }
              />
            </div>
          )
        })}
      </div>

      <div className="p7-settings-group">
        <h2 className="p7-settings-subtitle">Dependencies</h2>
        {catalog.dependencies.length === 0 && <P7SettingsEmpty>无依赖。</P7SettingsEmpty>}
        {catalog.dependencies.map((dependency) => {
          const installed = dependencyInstalled(snapshot, dependency)
          return (
            <P7SettingRow
              key={`${dependency.componentId}:${dependency.version}`}
              label={dependency.componentId}
              meta="Manifest-bound exact installed identity"
            >
              <P7SettingsStatus tone={installed ? 'ready' : 'error'}>
                {installed ? `${dependency.version} 已安装` : `${dependency.version} 未满足`}
              </P7SettingsStatus>
            </P7SettingRow>
          )
        })}
      </div>

      <div className="p7-settings-group">
        <h2 className="p7-settings-subtitle">Permissions & Budgets</h2>
        {catalog.operations.map((operation) => {
          const grant = requestedGrants.find((item) => item.action === operation)
          return (
            <div key={operation} className="p7-component-grant-draft">
              <div className="p7-component-grant-head">
                <P7SettingsToggle
                  label={operation}
                  checked={grant !== undefined}
                  disabled={busy}
                  onChange={(enabled) =>
                    setRequestedGrants((current) =>
                      enabled
                        ? Object.freeze([...current, defaultGrant(catalog, operation)])
                        : Object.freeze(current.filter((item) => item.action !== operation)),
                    )
                  }
                />
                <strong>{operation}</strong>
              </div>
              {grant !== undefined && (
                <div className="p7-component-grant-grid">
                  <label>
                    Logical resource
                    <input
                      value={grant.logicalResourceId ?? ''}
                      disabled={busy}
                      placeholder="optional logical id"
                      onChange={(event) =>
                        patchGrant(operation, {
                          logicalResourceId: event.target.value || null,
                          resourceVersion: event.target.value ? (grant.resourceVersion ?? 1) : null,
                        })
                      }
                    />
                  </label>
                  <label>
                    Resource version
                    <input
                      type="number"
                      min={1}
                      value={grant.resourceVersion ?? ''}
                      disabled={busy || grant.logicalResourceId === null}
                      onChange={(event) =>
                        patchGrant(operation, {
                          resourceVersion:
                            event.target.value === '' ? null : Number(event.target.value),
                        })
                      }
                    />
                  </label>
                  <label>
                    Logical service
                    <input
                      value={grant.logicalServiceId ?? ''}
                      disabled={busy || !catalog.network.required}
                      placeholder={catalog.network.required ? 'required' : 'no network'}
                      onChange={(event) =>
                        patchGrant(operation, { logicalServiceId: event.target.value || null })
                      }
                    />
                  </label>
                  <label>
                    Expiry (s)
                    <input
                      type="number"
                      min={60}
                      value={grant.expiresInSeconds}
                      disabled={busy}
                      onChange={(event) =>
                        patchGrant(operation, { expiresInSeconds: Number(event.target.value) })
                      }
                    />
                  </label>
                  <label>
                    Calls
                    <input
                      type="number"
                      min={1}
                      max={catalog.budgets.maxCalls}
                      value={grant.maximumInvocations}
                      disabled={busy}
                      onChange={(event) =>
                        patchGrant(operation, { maximumInvocations: Number(event.target.value) })
                      }
                    />
                  </label>
                  <label>
                    Bytes in
                    <input
                      type="number"
                      min={0}
                      max={catalog.budgets.maxBytesIn}
                      value={grant.maximumBytesIn}
                      disabled={busy}
                      onChange={(event) =>
                        patchGrant(operation, { maximumBytesIn: Number(event.target.value) })
                      }
                    />
                  </label>
                  <label>
                    Bytes out
                    <input
                      type="number"
                      min={0}
                      max={catalog.budgets.maxBytesOut}
                      value={grant.maximumBytesOut}
                      disabled={busy}
                      onChange={(event) =>
                        patchGrant(operation, { maximumBytesOut: Number(event.target.value) })
                      }
                    />
                  </label>
                  <label>
                    Tokens
                    <input
                      type="number"
                      min={0}
                      max={catalog.budgets.maxTokens}
                      value={grant.maximumTokens}
                      disabled={busy}
                      onChange={(event) =>
                        patchGrant(operation, { maximumTokens: Number(event.target.value) })
                      }
                    />
                  </label>
                  <label>
                    Wall ms
                    <input
                      type="number"
                      min={1}
                      max={catalog.budgets.maxWallTimeMs}
                      value={grant.maximumWallTimeMs}
                      disabled={busy}
                      onChange={(event) =>
                        patchGrant(operation, { maximumWallTimeMs: Number(event.target.value) })
                      }
                    />
                  </label>
                  <label>
                    Cost units
                    <input
                      type="number"
                      min={1}
                      max={catalog.budgets.maxCostUnits}
                      value={grant.maximumCostUnits}
                      disabled={busy}
                      onChange={(event) =>
                        patchGrant(operation, { maximumCostUnits: Number(event.target.value) })
                      }
                    />
                  </label>
                </div>
              )}
            </div>
          )
        })}
      </div>

      <div className="p7-component-review-actions">
        <button type="button" disabled={busy} onClick={onCancel}>
          <X size={13} />
          取消
        </button>
        <button
          type="button"
          className="p7-settings-primary"
          disabled={busy || !canSubmit}
          onClick={() =>
            onSubmit({
              requestedGrants,
              desiredConfiguration: configuration,
              desiredSlotBindings: slotBindings,
              dependencyGraph,
            })
          }
        >
          <ShieldCheck size={13} />
          提交 exact 草案
        </button>
      </div>
    </div>
  )
}

function P7ProposalControl({
  snapshot,
  catalog,
  action,
  busy,
  onPropose,
}: {
  readonly snapshot: DesktopWorkspaceComponentSnapshot
  readonly catalog: DesktopWorkspaceComponentCatalogItem
  readonly action: DesktopWorkspaceComponentLifecycleAction
  readonly busy: boolean
  readonly onPropose: P7WorkspaceComponentSettingsProps['onPropose']
}) {
  const [open, setOpen] = useState(false)
  if (!catalog.available) {
    return (
      <button type="button" disabled title="组件包尚未通过宿主登记与验证">
        <Boxes size={13} />
        登记组件包后可提出{ACTION_LABEL[action]}
      </button>
    )
  }
  return open ? (
    <P7ComponentProposalComposer
      snapshot={snapshot}
      catalog={catalog}
      action={action}
      busy={busy}
      onCancel={() => setOpen(false)}
      onSubmit={(draft) => {
        onPropose(catalog, action, draft)
        setOpen(false)
      }}
    />
  ) : (
    <button type="button" disabled={busy} onClick={() => setOpen(true)}>
      {action === 'install' ? (
        <Boxes size={13} />
      ) : action === 'disable' ? (
        <Unplug size={13} />
      ) : (
        <RefreshCw size={13} />
      )}
      配置并提出{ACTION_LABEL[action]}
    </button>
  )
}

const MCP_TOOLS = Object.freeze([
  'omnibase_files_list',
  'omnibase_files_read',
  'omnibase_files_hash',
  'omnibase_text_search',
] as const)

function P7InvokeControl({
  catalog,
  installation,
  busy,
  onInvoke,
}: {
  readonly catalog: DesktopWorkspaceComponentCatalogItem
  readonly installation: DesktopWorkspaceComponentInstallation
  readonly busy: boolean
  readonly onInvoke: P7WorkspaceComponentSettingsProps['onInvoke']
}) {
  const operation = catalog.operations[0]
  const [open, setOpen] = useState(false)
  const [slotId, setSlotId] = useState('')
  const [viewId, setViewId] = useState(catalog.componentId)
  const [task, setTask] = useState('')
  const [toolName, setToolName] = useState<(typeof MCP_TOOLS)[number]>('omnibase_files_list')
  const [path, setPath] = useState('')
  const [query, setQuery] = useState('')
  const [artifactIds, setArtifactIds] = useState('')
  const [logicalId, setLogicalId] = useState('')

  if (operation === undefined) return null
  if (!open) {
    return (
      <button
        type="button"
        disabled={busy || installation.health !== 'healthy' || installation.state !== 'active'}
        onClick={() => setOpen(true)}
      >
        <Play size={13} />
        运行
      </button>
    )
  }

  const hostSlots = catalog.slots.filter((slot) => p7WorkspaceComponentHostSlotId(slot.slotId))
  const artifacts = artifactIds
    .split(/[\s,]+/u)
    .map((value) => value.trim())
    .filter(Boolean)
  const artifactsValid = artifacts.every((value) => /^[a-z][a-z0-9_.:-]{1,127}$/u.test(value))
  const pathRequired = toolName === 'omnibase_files_read' || toolName === 'omnibase_files_hash'
  const queryRequired = toolName === 'omnibase_text_search'
  const canInvoke =
    operation === 'ui.render'
      ? p7WorkspaceComponentHostSlotId(slotId) &&
        hostSlots.some((slot) => slot.slotId === slotId) &&
        /^[a-z][a-z0-9_.:-]{1,127}$/u.test(viewId)
      : operation === 'skill.resolve'
        ? task.trim().length > 0 && task.length <= 32_768
        : operation === 'mcp.call'
          ? (!pathRequired || path.trim().length > 0) && (!queryRequired || query.trim().length > 0)
          : operation === 'sandbox.run'
            ? artifactsValid
            : operation === 'local_adapter.open'
              ? logicalId === '' || /^[a-z][a-z0-9_.:-]{1,127}$/u.test(logicalId)
              : false

  const submit = () => {
    if (!canInvoke) return
    if (operation === 'ui.render') {
      onInvoke(installation, { operation, arguments: { slotId, viewId } })
    } else if (operation === 'skill.resolve') {
      onInvoke(installation, {
        operation,
        arguments: { skillId: catalog.componentId, task: task.trim() },
      })
    } else if (operation === 'mcp.call') {
      onInvoke(installation, {
        operation,
        arguments: {
          toolName,
          ...(path === '' ? {} : { path }),
          ...(query === '' ? {} : { query }),
        },
      })
    } else if (operation === 'sandbox.run') {
      onInvoke(installation, {
        operation,
        arguments: { workloadId: 'bounded-transform', inputArtifactIds: artifacts },
      })
    } else if (operation === 'local_adapter.open') {
      onInvoke(installation, {
        operation,
        arguments: {
          adapterId: 'knowledge.ebook',
          destination: 'workspace',
          ...(logicalId === '' ? {} : { logicalId }),
        },
      })
    }
    setOpen(false)
  }

  return (
    <div className="p7-component-invoke-control">
      <div className="p7-component-invoke-head">
        <strong>{operation}</strong>
        <button
          type="button"
          aria-label="关闭调用输入"
          disabled={busy}
          onClick={() => setOpen(false)}
        >
          <X size={13} />
        </button>
      </div>
      {operation === 'ui.render' && (
        <div className="p7-component-invoke-grid">
          <label>
            Slot
            <select
              value={slotId}
              disabled={busy}
              onChange={(event) => setSlotId(event.target.value)}
            >
              <option value="">选择组件 Slot</option>
              {hostSlots.map((slot) => (
                <option key={slot.slotId} value={slot.slotId}>
                  {slot.slotId}
                </option>
              ))}
            </select>
          </label>
          <label>
            View ID
            <input
              value={viewId}
              disabled={busy}
              onChange={(event) => setViewId(event.target.value)}
            />
          </label>
        </div>
      )}
      {operation === 'skill.resolve' && (
        <label>
          Task
          <textarea
            value={task}
            maxLength={32_768}
            disabled={busy}
            onChange={(event) => setTask(event.target.value)}
          />
        </label>
      )}
      {operation === 'mcp.call' && (
        <div className="p7-component-invoke-grid">
          <label>
            Tool
            <select
              value={toolName}
              disabled={busy}
              onChange={(event) => setToolName(event.target.value as (typeof MCP_TOOLS)[number])}
            >
              {MCP_TOOLS.map((tool) => (
                <option key={tool} value={tool}>
                  {tool}
                </option>
              ))}
            </select>
          </label>
          <label>
            Logical path
            <input
              value={path}
              disabled={busy}
              placeholder={pathRequired ? 'required' : 'root when empty'}
              onChange={(event) => setPath(event.target.value)}
            />
          </label>
          <label>
            Query
            <input
              value={query}
              disabled={busy}
              placeholder={queryRequired ? 'required' : 'not used'}
              onChange={(event) => setQuery(event.target.value)}
            />
          </label>
        </div>
      )}
      {operation === 'sandbox.run' && (
        <label>
          Input artifact IDs
          <input
            value={artifactIds}
            disabled={busy}
            placeholder="comma or space separated; empty is allowed"
            onChange={(event) => setArtifactIds(event.target.value)}
          />
        </label>
      )}
      {operation === 'local_adapter.open' && (
        <div className="p7-component-invoke-grid">
          <label>
            Adapter
            <input value="knowledge.ebook" disabled />
          </label>
          <label>
            Destination
            <input value="workspace" disabled />
          </label>
          <label>
            Logical ID
            <input
              value={logicalId}
              disabled={busy}
              placeholder="optional"
              onChange={(event) => setLogicalId(event.target.value)}
            />
          </label>
        </div>
      )}
      <button
        type="button"
        className="p7-settings-primary"
        disabled={busy || !canInvoke}
        onClick={submit}
      >
        <Play size={13} />
        执行受控调用
      </button>
    </div>
  )
}

export function P7ComponentCatalogView(props: P7WorkspaceComponentSettingsProps) {
  return (
    <P7SettingsSection title="Catalog" scope="当前 Workspace">
      {props.snapshot === null ? (
        <LoadingState status={props.status} />
      ) : (
        <>
          <div className="p7-component-catalog-toolbar">
            <div>
              <strong>Owner-reviewed package</strong>
              <span>选择由 Electron 校验并登记的本地声明式组件包。</span>
            </div>
            <button
              type="button"
              className="p7-settings-primary"
              disabled={props.busy || props.workspaceId === null}
              onClick={props.onImportOwnerPackage}
            >
              <PackagePlus size={13} />
              {props.busy ? '正在处理…' : '选择并登记组件包'}
            </button>
          </div>
          <div className="p7-component-list">
            {props.snapshot.catalog.map((item) => {
              const installed = installationFor(props.snapshot!, item.componentId)
              const actions = p7WorkspaceComponentLifecycleActions(item, installed)
              return (
                <ComponentRow
                  key={`${item.componentId}:${item.version}`}
                  item={item}
                  trailing={
                    <div className="p7-component-row-actions">
                      <P7SettingsStatus
                        tone={
                          !item.available
                            ? 'error'
                            : installed === null
                              ? 'muted'
                              : statusTone(installed.state)
                        }
                      >
                        {installed?.state ?? (item.available ? '可安装' : '包未登记')}
                      </P7SettingsStatus>
                      {actions.map((action) => (
                        <P7ProposalControl
                          key={action}
                          snapshot={props.snapshot!}
                          catalog={item}
                          action={action}
                          busy={props.busy}
                          onPropose={props.onPropose}
                        />
                      ))}
                    </div>
                  }
                />
              )
            })}
            {props.snapshot.catalog.length === 0 && (
              <P7SettingsEmpty>Catalog 为空。</P7SettingsEmpty>
            )}
          </div>
        </>
      )}
    </P7SettingsSection>
  )
}

export function P7InstalledComponentsView(props: P7WorkspaceComponentSettingsProps) {
  return (
    <P7SettingsSection title="Installed" scope={props.workspaceName}>
      {props.snapshot === null ? (
        <LoadingState status={props.status} />
      ) : (
        <div className="p7-installation-list">
          {props.snapshot.installations
            .filter((item) => item.state !== 'uninstalled')
            .map((item) => {
              const catalog = catalogFor(props.snapshot!, item.componentId, item.version)
              const actions =
                catalog === null
                  ? Object.freeze([])
                  : p7WorkspaceComponentLifecycleActions(catalog, item)
              return (
                <div key={item.installationId} className="p7-installation-row">
                  <div className="p7-component-identity">
                    <strong>{catalog?.displayName ?? item.componentId}</strong>
                    <span>
                      {item.componentId} · generation {item.bindingGeneration}
                    </span>
                  </div>
                  <span>{item.version}</span>
                  <P7SettingsStatus tone={statusTone(item.state)}>{item.state}</P7SettingsStatus>
                  <P7SettingsStatus tone={statusTone(item.health)}>{item.health}</P7SettingsStatus>
                  <div className="p7-component-row-actions">
                    {catalog !== null && item.state === 'active' && (
                      <P7InvokeControl
                        catalog={catalog}
                        installation={item}
                        busy={props.busy}
                        onInvoke={props.onInvoke}
                      />
                    )}
                    {catalog !== null &&
                      actions.map((action) => (
                        <P7ProposalControl
                          key={action}
                          snapshot={props.snapshot!}
                          catalog={catalog}
                          action={action}
                          busy={props.busy}
                          onPropose={props.onPropose}
                        />
                      ))}
                  </div>
                </div>
              )
            })}
          {props.snapshot.installations.length === 0 && (
            <P7SettingsEmpty>当前 Workspace 未安装组件。</P7SettingsEmpty>
          )}
        </div>
      )}
    </P7SettingsSection>
  )
}

export function P7SlotsView(props: P7WorkspaceComponentSettingsProps) {
  const rows =
    props.snapshot?.catalog.flatMap((item) => (item.slots ?? []).map((slot) => ({ item, slot }))) ??
    []
  return (
    <P7SettingsSection title="Slots" scope="Host-owned">
      {props.snapshot === null ? (
        <LoadingState status={props.status} />
      ) : (
        <div className="p7-component-list">
          {rows.map(({ item, slot }) => (
            <div
              key={`${item.componentId}:${item.version}:${slot.slotId}`}
              className="p7-slot-binding-row"
            >
              <Braces size={14} />
              <strong>{slot.slotId}</strong>
              <span>{item.displayName}</span>
              <span>
                {slot.cardinality} · {slot.minimumOrder}–{slot.maximumOrder}
              </span>
              <P7SettingsStatus
                tone={
                  installationFor(props.snapshot!, item.componentId)?.state === 'active'
                    ? 'ready'
                    : 'muted'
                }
              >
                {installationFor(props.snapshot!, item.componentId)?.state ?? 'catalog'}
              </P7SettingsStatus>
            </div>
          ))}
          {rows.length === 0 && (
            <P7SettingsEmpty>当前 catalog 没有已验证的 Slot 投影。</P7SettingsEmpty>
          )}
        </div>
      )}
    </P7SettingsSection>
  )
}

function P7FamilyView(
  props: P7WorkspaceComponentSettingsProps,
  family: DesktopWorkspaceComponentCatalogItem['family'],
  title: string,
) {
  const items = props.snapshot?.catalog.filter((item) => item.family === family) ?? []
  return (
    <P7SettingsSection title={title} scope="统一组件生命周期">
      {props.snapshot === null ? (
        <LoadingState status={props.status} />
      ) : (
        <div className="p7-component-list">
          {items.map((item) => {
            const installation = installationFor(props.snapshot!, item.componentId)
            const actions = p7WorkspaceComponentLifecycleActions(item, installation)
            return (
              <ComponentRow
                key={`${item.componentId}:${item.version}`}
                item={item}
                trailing={
                  <div className="p7-component-row-actions">
                    <P7SettingsStatus
                      tone={
                        !item.available
                          ? 'error'
                          : installation === null
                            ? 'muted'
                            : statusTone(installation.state)
                      }
                    >
                      {installation?.state ?? (item.available ? '可安装' : '包未登记')}
                    </P7SettingsStatus>
                    {installation?.state === 'active' && (
                      <P7InvokeControl
                        catalog={item}
                        installation={installation}
                        busy={props.busy}
                        onInvoke={props.onInvoke}
                      />
                    )}
                    {actions.map((action) => (
                      <P7ProposalControl
                        key={action}
                        snapshot={props.snapshot!}
                        catalog={item}
                        action={action}
                        busy={props.busy}
                        onPropose={props.onPropose}
                      />
                    ))}
                  </div>
                }
              />
            )
          })}
          {items.length === 0 && <P7SettingsEmpty>此组件族当前没有 catalog 条目。</P7SettingsEmpty>}
        </div>
      )}
    </P7SettingsSection>
  )
}

export const P7SkillsView = (props: P7WorkspaceComponentSettingsProps) =>
  P7FamilyView(props, 'instruction_skill', 'Skills')
export const P7McpView = (props: P7WorkspaceComponentSettingsProps) =>
  P7FamilyView(props, 'mcp_connector', 'MCP')
export const P7SandboxView = (props: P7WorkspaceComponentSettingsProps) =>
  P7FamilyView(props, 'sandbox_workload', 'Sandbox')
export const P7LocalAdaptersView = (props: P7WorkspaceComponentSettingsProps) =>
  P7FamilyView(props, 'trusted_local_adapter', 'Local Adapters')

export function P7PermissionsView(props: P7WorkspaceComponentSettingsProps) {
  const requests =
    props.snapshot?.proposals.flatMap((proposal) =>
      proposal.requestedGrants.map((grant, index) => ({ proposal, grant, index })),
    ) ?? []
  return (
    <P7SettingsSection title="Permissions" scope="Live grants / revocations / proposal requests">
      {props.snapshot === null ? (
        <LoadingState status={props.status} />
      ) : (
        <>
          <h2 className="p7-settings-subtitle">现行授权</h2>
          <div className="p7-permission-list">
            {props.snapshot.grants.map((grant) => (
              <div key={grant.grantId} className="p7-permission-row">
                <FileKey size={14} />
                <div className="p7-component-identity">
                  <strong>{grant.componentId}</strong>
                  <span>{grant.actions.join(', ')}</span>
                </div>
                <span>{grant.requiresNetwork ? '受控网络' : '无网络'}</span>
                <span>
                  {grant.remaining.calls} / {grant.limits.calls} calls
                </span>
                <span>{grant.remaining.bytesOut} B out</span>
                <span>{grant.remaining.tokens} tokens</span>
                <span>{grant.remaining.wallTimeMs} ms</span>
                <P7SettingsStatus tone={grant.state === 'active' ? 'ready' : 'error'}>
                  {grant.state}
                </P7SettingsStatus>
                <time title={grant.notBefore}>至 {grant.expiresAt}</time>
              </div>
            ))}
            {props.snapshot.grants.length === 0 && (
              <P7SettingsEmpty>没有现行授权。</P7SettingsEmpty>
            )}
          </div>
          <h2 className="p7-settings-subtitle">撤销记录</h2>
          <div className="p7-permission-list">
            {props.snapshot.revocations.map((revocation) => (
              <div key={revocation.revocationId} className="p7-permission-row">
                <Ban size={14} />
                <div className="p7-component-identity">
                  <strong>{revocation.reasonCode}</strong>
                  <span>
                    {revocation.grantId ??
                      revocation.runtimeInstanceId ??
                      revocation.installationId}
                  </span>
                </div>
                <span>{revocation.actorType}</span>
                <time>{revocation.createdAt}</time>
              </div>
            ))}
            {props.snapshot.revocations.length === 0 && (
              <P7SettingsEmpty>没有撤销记录。</P7SettingsEmpty>
            )}
          </div>
          <h2 className="p7-settings-subtitle">提案请求（不等于授权）</h2>
          <div className="p7-permission-list">
            {requests.map(({ proposal, grant, index }) => (
              <div key={`${proposal.proposalId}:${index}`} className="p7-permission-row">
                <ShieldAlert size={14} />
                <div className="p7-component-identity">
                  <strong>{grant.action}</strong>
                  <span>{proposal.componentId}</span>
                </div>
                <span>{grant.logicalResourceId ?? grant.logicalServiceId ?? '无逻辑资源'}</span>
                <span>{grant.maximumInvocations} calls</span>
                <span>{grant.maximumWallTimeMs} ms</span>
                <P7SettingsStatus tone={proposal.decision === 'rejected' ? 'error' : 'warning'}>
                  {proposal.decision === 'approved' ? '已批准提案' : (proposal.decision ?? '待审')}
                </P7SettingsStatus>
              </div>
            ))}
            {requests.length === 0 && <P7SettingsEmpty>没有能力请求。</P7SettingsEmpty>}
          </div>
        </>
      )}
    </P7SettingsSection>
  )
}

export function P7HealthView(props: P7WorkspaceComponentSettingsProps) {
  const activeOperations =
    props.snapshot?.operations.filter((item) => item.state === 'pending').length ?? 0
  const managedComponentCount =
    props.snapshot?.installations.filter(
      (item) => item.state === 'active' || item.state === 'bound' || item.state === 'disabled',
    ).length ?? 0
  const canStop = p7EmergencyStopEligible({
    viewWorkspaceId: props.workspaceId,
    snapshotWorkspaceId: props.snapshot?.workspaceId ?? null,
    activeOperationCount: activeOperations,
    managedComponentCount,
    stopInFlight: props.busy,
  })
  return (
    <P7SettingsSection title="Health" scope="实时持久状态">
      {props.snapshot === null ? (
        <LoadingState status={props.status} />
      ) : (
        <>
          <div className="p7-emergency-row">
            <ShieldAlert size={16} />
            <div>
              <strong>Workspace 紧急停止</strong>
              <span>fence 并停止所有非核心组件；标准工作台和设置保持可用。</span>
            </div>
            <button
              type="button"
              className="p7-danger-button"
              disabled={!canStop}
              onClick={props.onEmergencyStop}
            >
              <CircleStop size={14} />
              全部停止
            </button>
          </div>
          <div className="p7-health-list">
            {props.snapshot.installations
              .filter((item) => item.state !== 'uninstalled')
              .map((item) => (
                <div key={item.installationId} className="p7-health-row">
                  <Activity size={14} />
                  <div className="p7-component-identity">
                    <strong>{item.componentId}</strong>
                    <span>generation {item.bindingGeneration}</span>
                  </div>
                  <P7SettingsStatus tone={statusTone(item.state)}>{item.state}</P7SettingsStatus>
                  <P7SettingsStatus tone={statusTone(item.health)}>{item.health}</P7SettingsStatus>
                  <span>{item.lastErrorCode ?? '无错误'}</span>
                  <time>{item.updatedAt}</time>
                </div>
              ))}
          </div>
        </>
      )}
    </P7SettingsSection>
  )
}

export function P7ComponentReviewView(props: P7WorkspaceComponentSettingsProps) {
  const proposals = props.snapshot?.proposals ?? []
  return (
    <P7SettingsSection
      title="Review"
      scope={`${proposals.filter((item) => item.decision === null).length} 个待决`}
    >
      {props.snapshot === null ? (
        <LoadingState status={props.status} />
      ) : (
        <div className="p7-component-review-list">
          <div className="p7-component-assistant-proposal">
            <Bot size={16} />
            <div>
              <strong>Agent 组件工作流</strong>
              <span>生成新声明式包，或从 Catalog 生成生命周期提案。</span>
            </div>
            <textarea
              value={props.assistantIntent}
              maxLength={2_000}
              disabled={props.busy}
              placeholder="描述要创建、安装、升级、停用或调整的 Workspace 组件…"
              onChange={(event) => props.onAssistantIntentChange(event.target.value)}
            />
            <div className="p7-component-assistant-actions">
              <button
                type="button"
                disabled={props.busy || props.assistantIntent.trim() === ''}
                onClick={props.onRequestAssistantPackage}
              >
                <PackagePlus size={13} />
                生成声明式包
              </button>
              <button
                type="button"
                className="p7-settings-primary"
                disabled={props.busy || props.assistantIntent.trim() === ''}
                onClick={props.onRequestAssistantProposal}
              >
                <Sparkles size={13} />
                生成生命周期提案
              </button>
            </div>
          </div>
          {props.assistantPackageReview !== null && (
            <article className="p7-component-review-row p7-assistant-package-review">
              <div className="p7-component-review-head">
                <div className="p7-component-identity">
                  <strong>
                    {props.assistantPackageReview.componentId}@
                    {props.assistantPackageReview.version}
                  </strong>
                  <span>publisher {props.assistantPackageReview.publisherId} · Owner 待登记</span>
                </div>
                <P7SettingsStatus tone="warning">未登记 / 未安装</P7SettingsStatus>
              </div>
              <div className="p7-component-binding-review">
                {props.assistantPackageReview.slots.map((slot) => (
                  <div key={slot}>
                    <strong>Slot</strong>
                    <span>{slot}</span>
                  </div>
                ))}
                {props.assistantPackageReview.sections.map((section) => (
                  <div key={section.id}>
                    <strong>{section.label}</strong>
                    <span>
                      {section.id} · source {section.source}
                    </span>
                  </div>
                ))}
              </div>
              <div className="p7-component-review-digests">
                <span>Package manifest</span>
                <Digest>{props.assistantPackageReview.manifestSha256}</Digest>
                <span>Package</span>
                <Digest>{props.assistantPackageReview.packageSha256}</Digest>
              </div>
              <div className="p7-component-review-actions">
                <button
                  type="button"
                  disabled={props.busy}
                  onClick={props.onDiscardAssistantPackage}
                >
                  <X size={13} />
                  丢弃
                </button>
                <button
                  type="button"
                  className="p7-settings-primary"
                  disabled={props.busy}
                  onClick={props.onRegisterAssistantPackage}
                >
                  <ShieldCheck size={13} />
                  Owner 登记 exact SHA
                </button>
              </div>
            </article>
          )}
          {proposals.map((proposal) => {
            const installation = installationFor(props.snapshot!, proposal.componentId)
            const catalog = catalogFor(
              props.snapshot!,
              proposal.componentId,
              proposal.targetVersion,
            )
            const settingsSchema = p7ParseDeclarativeSettingsSchema(catalog?.settingsSchema)
            const configurationDiff =
              settingsSchema === null
                ? []
                : p7DeclarativeSettingsDiff(
                    settingsSchema,
                    declarativeSettings(installation?.desiredConfiguration ?? {}),
                    declarativeSettings(proposal.desiredConfiguration),
                  )
            const stale = proposal.baseRevision !== (installation?.revision ?? 0)
            const executed = proposalExecuted(props.snapshot!, proposal)
            return (
              <div key={proposal.proposalId} className="p7-component-review-row">
                <div className="p7-component-review-head">
                  <div className="p7-component-identity">
                    <strong>
                      {ACTION_LABEL[proposal.changeKind]} · {proposal.componentId}
                    </strong>
                    <span>
                      {proposal.sourceKind} · {proposal.sourceReference ?? 'owner gesture'} · base r
                      {proposal.baseRevision} → {proposal.targetVersion}
                    </span>
                  </div>
                  <P7SettingsStatus
                    tone={
                      stale
                        ? 'error'
                        : proposal.decision === 'approved'
                          ? 'ready'
                          : proposal.decision === 'rejected'
                            ? 'error'
                            : 'warning'
                    }
                  >
                    {stale ? '基线过期' : (proposal.decision ?? '待审')}
                  </P7SettingsStatus>
                </div>
                <div className="p7-component-exact-diff">
                  <span>Lifecycle</span>
                  <del>{installation?.state ?? '未安装'}</del>
                  <span>→</span>
                  <ins>{proposal.changeKind}</ins>
                  <span>Version</span>
                  <del>{installation?.version ?? '—'}</del>
                  <span>→</span>
                  <ins>{proposal.targetVersion}</ins>
                  <span>Grants</span>
                  <del>当前绑定</del>
                  <span>→</span>
                  <ins>{proposal.requestedGrants.length} 项请求</ins>
                  <span>Slots</span>
                  <del>当前绑定</del>
                  <span>→</span>
                  <ins>{proposal.desiredSlotBindings.length} 项</ins>
                  <span>Dependencies</span>
                  <del>当前图</del>
                  <span>→</span>
                  <ins>{proposal.dependencyGraph.length} 项</ins>
                </div>
                {configurationDiff.length > 0 && (
                  <div className="p7-component-diff-detail">
                    {configurationDiff.map((row) => (
                      <div key={row.key}>
                        <span>{row.label}</span>
                        <del>{row.before}</del>
                        <span>→</span>
                        <ins>{row.after}</ins>
                      </div>
                    ))}
                  </div>
                )}
                <div className="p7-component-binding-review">
                  {proposal.desiredSlotBindings.map((binding) => (
                    <div key={`${binding.slotId}:${binding.bindingKey}`}>
                      <strong>{binding.slotId}</strong>
                      <span>
                        {binding.bindingKey} · order {binding.orderIndex}
                      </span>
                    </div>
                  ))}
                  {proposal.dependencyGraph.map((dependency) => (
                    <div key={`${dependency.componentId}:${dependency.version}`}>
                      <strong>{dependency.componentId}</strong>
                      <span>
                        {dependency.version} · policy {dependency.policyManifestSha256.slice(0, 12)}{' '}
                        · manifest {dependency.manifestSha256.slice(0, 12)}
                      </span>
                    </div>
                  ))}
                </div>
                <div className="p7-component-review-digests">
                  <span>Request</span>
                  <Digest>{proposal.requestSha256}</Digest>
                  <span>Policy</span>
                  <Digest>{catalog?.policyManifestSha256 ?? 'catalog identity unavailable'}</Digest>
                  <span>Package manifest</span>
                  <Digest>{proposal.manifestSha256}</Digest>
                  <span>Package</span>
                  <Digest>{proposal.packageSha256}</Digest>
                </div>
                <div className="p7-component-review-actions">
                  {proposal.decision === null ? (
                    <>
                      <button
                        type="button"
                        disabled={props.busy}
                        onClick={() => props.onDecide(proposal, 'reject')}
                      >
                        <X size={13} />
                        拒绝
                      </button>
                      <button
                        type="button"
                        className="p7-settings-primary"
                        disabled={props.busy || stale}
                        onClick={() => props.onDecide(proposal, 'approve')}
                      >
                        <Check size={13} />
                        批准 exact SHA
                      </button>
                    </>
                  ) : proposal.decision === 'approved' && !executed ? (
                    <button
                      type="button"
                      className="p7-settings-primary"
                      disabled={props.busy || stale}
                      onClick={() => props.onAction(proposal)}
                    >
                      <Play size={13} />
                      执行{ACTION_LABEL[proposal.changeKind]}
                    </button>
                  ) : (
                    <span>{executed ? '生命周期操作已记录' : '提案已终止'}</span>
                  )}
                </div>
              </div>
            )
          })}
          {proposals.length === 0 && <P7SettingsEmpty>没有组件提案。</P7SettingsEmpty>}
        </div>
      )}
    </P7SettingsSection>
  )
}

export function P7ComponentAuditView(props: P7WorkspaceComponentSettingsProps) {
  const events = props.snapshot?.audit ?? []
  return (
    <P7SettingsSection title="Audit" scope="Append-only projections">
      {props.snapshot === null ? (
        <LoadingState status={props.status} />
      ) : (
        <div className="p7-component-audit-list">
          {events.map((event) => (
            <div key={event.eventId} className="p7-component-audit-row">
              <History size={14} />
              <strong>
                #{event.sequence} · {event.eventType}
              </strong>
              <Digest>{event.eventId}</Digest>
              <time>{event.createdAt}</time>
            </div>
          ))}
          {events.length === 0 && <P7SettingsEmpty>没有组件生命周期记录。</P7SettingsEmpty>}
        </div>
      )}
    </P7SettingsSection>
  )
}

export function P7RecoveryView(props: P7WorkspaceComponentSettingsProps) {
  const [evidenceByEffect, setEvidenceByEffect] = useState<Record<string, string>>({})
  const recoveryEffectIds = new Set(
    props.snapshot?.recoveries.map((recovery) => recovery.effectId) ?? [],
  )
  const effects =
    props.snapshot?.effects.filter(
      (effect) =>
        !recoveryEffectIds.has(effect.effectId) &&
        p7ComponentEffectNeedsReconciliation(effect.state === 'none' ? 'failed' : effect.state),
    ) ?? []
  return (
    <P7SettingsSection title="Recovery" scope="No automatic replay">
      {props.snapshot === null ? (
        <LoadingState status={props.status} />
      ) : (
        <div className="p7-recovery-list">
          {props.snapshot.recoveries.map((recovery) => {
            const effect = props.snapshot!.effects.find(
              (item) => item.effectId === recovery.effectId,
            )
            const evidence = evidenceByEffect[recovery.effectId] ?? ''
            const validEvidence = /^[a-f0-9]{64}$/.test(evidence)
            const actionable =
              p7ComponentEffectNeedsReconciliation(recovery.state) &&
              effect !== undefined &&
              p7ComponentEffectNeedsReconciliation(
                effect.state === 'none' ? 'failed' : effect.state,
              )
            return (
              <div key={recovery.recoveryId} className="p7-recovery-row">
                <ShieldAlert size={14} />
                <div className="p7-component-identity">
                  <strong>{recovery.componentId}</strong>
                  <span>
                    {recovery.adapterId} · generation {recovery.bindingGeneration} ·{' '}
                    {recovery.state}
                  </span>
                </div>
                <Digest>{recovery.operationId}</Digest>
                <span>{recovery.reasonCode}</span>
                <time>{recovery.createdAt}</time>
                <div className="p7-recovery-identities">
                  <span>Runtime</span>
                  <Digest>{recovery.runtimeInstanceId}</Digest>
                  <span>Workload</span>
                  <Digest>{recovery.workloadIdentityDigest}</Digest>
                </div>
                {actionable && (
                  <div className="p7-recovery-reconcile">
                    <input
                      value={evidence}
                      maxLength={64}
                      placeholder="evidence SHA-256"
                      onChange={(event) =>
                        setEvidenceByEffect((current) => ({
                          ...current,
                          [recovery.effectId]: event.target.value.trim().toLowerCase(),
                        }))
                      }
                    />
                    <button
                      type="button"
                      disabled={props.busy || !validEvidence}
                      onClick={() => props.onReconcile(effect, 'failed', evidence)}
                    >
                      <Ban size={13} />
                      确认失败
                    </button>
                    <button
                      type="button"
                      className="p7-settings-primary"
                      disabled={props.busy || !validEvidence}
                      onClick={() => props.onReconcile(effect, 'succeeded', evidence)}
                    >
                      <ShieldCheck size={13} />
                      确认成功
                    </button>
                  </div>
                )}
              </div>
            )
          })}
          {effects.length > 0 && <h2 className="p7-settings-subtitle">Ambiguous effects</h2>}
          {effects.map((effect) => {
            const operation = props.snapshot!.operations.find(
              (item) => item.operationId === effect.operationId,
            )
            const evidence = evidenceByEffect[effect.effectId] ?? ''
            const validEvidence = /^[a-f0-9]{64}$/.test(evidence)
            return (
              <div key={effect.effectId} className="p7-recovery-row">
                <ShieldAlert size={14} />
                <div className="p7-component-identity">
                  <strong>{effect.componentId}</strong>
                  <span>
                    {effect.effectId} · {effect.state}
                  </span>
                </div>
                <input
                  value={evidence}
                  maxLength={64}
                  placeholder="evidence SHA-256"
                  onChange={(event) =>
                    setEvidenceByEffect((current) => ({
                      ...current,
                      [effect.effectId]: event.target.value.trim().toLowerCase(),
                    }))
                  }
                />
                <button
                  type="button"
                  disabled={props.busy || !validEvidence || operation === undefined}
                  onClick={() => props.onReconcile(effect, 'failed', evidence)}
                >
                  <Ban size={13} />
                  确认失败
                </button>
                <button
                  type="button"
                  className="p7-settings-primary"
                  disabled={props.busy || !validEvidence || operation === undefined}
                  onClick={() => props.onReconcile(effect, 'succeeded', evidence)}
                >
                  <ShieldCheck size={13} />
                  确认成功
                </button>
              </div>
            )
          })}
          {effects.length === 0 && props.snapshot.recoveries.length === 0 && (
            <P7SettingsEmpty>没有恢复阻塞或待 reconciliation 的 effect。</P7SettingsEmpty>
          )}
        </div>
      )}
    </P7SettingsSection>
  )
}

export function P7WorkspaceComponentView({
  section,
  ...props
}: P7WorkspaceComponentSettingsProps & { readonly section: P7WorkspaceComponentSettingsSection }) {
  switch (section) {
    case 'catalog':
      return <P7ComponentCatalogView {...props} />
    case 'installed':
      return <P7InstalledComponentsView {...props} />
    case 'slots':
      return <P7SlotsView {...props} />
    case 'skills':
      return <P7SkillsView {...props} />
    case 'mcp':
      return <P7McpView {...props} />
    case 'sandbox':
      return <P7SandboxView {...props} />
    case 'local-adapters':
      return <P7LocalAdaptersView {...props} />
    case 'permissions':
      return <P7PermissionsView {...props} />
    case 'health':
      return <P7HealthView {...props} />
    case 'component-review':
      return <P7ComponentReviewView {...props} />
    case 'component-audit':
      return <P7ComponentAuditView {...props} />
    case 'recovery':
      return <P7RecoveryView {...props} />
  }
}
