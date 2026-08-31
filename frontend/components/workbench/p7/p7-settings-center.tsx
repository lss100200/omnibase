'use client'

import {
  Activity,
  AppWindow,
  Blocks,
  Bot,
  Check,
  CircleGauge,
  Download,
  KeyRound,
  Monitor,
  Network,
  Package,
  PanelTop,
  PlugZap,
  RotateCcw,
  Save,
  ScrollText,
  ShieldCheck,
  SlidersHorizontal,
  Sparkles,
  SquareDashed,
  Wrench,
  X,
} from 'lucide-react'
import { useEffect, useId, useRef, useState } from 'react'

import type {
  DesktopApplicationPreference,
  DesktopProvider,
  DesktopReasoningGear,
  DesktopThinkingDepth,
  DesktopWorkbenchDensity,
  DesktopWorkspaceCompositionAuditEvent,
  DesktopWorkspaceCompositionProfileValue,
  DesktopWorkspaceCompositionProposal,
  DesktopWorkspaceCompositionSnapshot,
  DesktopWorkspaceSlotCatalogItem,
} from '@/lib/desktop-bridge'
import {
  p7CompositionDiff,
  p7CompositionLayoutChoiceEnabled,
  p7CompositionProposalReview,
  p7PatchCompositionProfile,
  type P7CompositionLoadStatus,
} from '@/lib/p7-workspace-composition'
import {
  p7SettingsNavigationTargetIndex,
  type P7SettingsNavigationKey,
} from '@/lib/p7-settings-accessibility'
import type { P7WorkspaceComponentSurfaceProjection } from '@/lib/p7-workspace-components'
import { P7ComponentSurface } from './p7-component-surface'
import {
  P7Segmented as Segmented,
  P7SettingRow as SettingRow,
  P7SettingsToggle as Toggle,
} from './settings/p7-settings-shared'
import {
  P7WorkspaceComponentView,
  type P7WorkspaceComponentSettingsProps,
  type P7WorkspaceComponentSettingsSection,
} from './settings/p7-workspace-component-views'

export interface P7ProviderForm {
  readonly displayName: string
  readonly baseUrl: string
  readonly apiKey: string
  readonly modelName: string
  readonly gear: DesktopReasoningGear
  readonly thinkingDepth: DesktopThinkingDepth
  readonly timeoutSeconds: number
  readonly allowLoopbackHttp: boolean
  readonly isDefault: boolean
  readonly isEnabled: boolean
}

export interface P7SettingsCenterProps extends P7WorkspaceComponentSettingsProps {
  readonly applicationPreference: DesktopApplicationPreference | null
  readonly applicationPreferenceStatus: P7CompositionLoadStatus
  readonly onApplicationPreferenceChange: (
    density: DesktopWorkbenchDensity,
    reduceMotion: boolean,
  ) => void
  readonly compositionStatus: P7CompositionLoadStatus
  readonly compositionSnapshot: DesktopWorkspaceCompositionSnapshot | null
  readonly compositionDraft: DesktopWorkspaceCompositionProfileValue | null
  readonly onCompositionDraftChange: (profile: DesktopWorkspaceCompositionProfileValue) => void
  readonly onCreateCompositionProposal: () => void
  readonly onRequestAssistantComposition: () => void
  readonly compositionIntent: string
  readonly onCompositionIntentChange: (value: string) => void
  readonly onDecideCompositionProposal: (
    proposal: DesktopWorkspaceCompositionProposal,
    decision: 'approve' | 'reject',
  ) => void
  readonly onProposeCompositionRollback: (targetRevision: number) => void
  readonly compositionBusy: boolean
  readonly compositionNotice: string | null
  readonly workspaceFilesAuthorized: boolean
  readonly bridgeSubscribed: boolean
  readonly providerForm: P7ProviderForm
  readonly onProviderFormChange: (patch: Partial<P7ProviderForm>) => void
  readonly onSaveProvider: () => void
  readonly submitting: boolean
  readonly testResult: string | null
  readonly providers: readonly DesktopProvider[]
  readonly onTestProvider: (providerId: string) => void
}

type SettingsSection =
  | 'appearance'
  | 'workspace'
  | 'layout-components'
  | 'capabilities'
  | 'providers'
  | 'profile-review'
  | 'profile-audit'
  | 'component-extension'
  | P7WorkspaceComponentSettingsSection

const SECTIONS = [
  { id: 'appearance', label: '外观', icon: Monitor },
  { id: 'workspace', label: 'Workspace', icon: SlidersHorizontal },
  { id: 'layout-components', label: '布局组件', icon: Blocks },
  { id: 'capabilities', label: '能力', icon: Activity },
  { id: 'providers', label: 'Provider', icon: KeyRound },
  { id: 'profile-review', label: '布局审阅', icon: ShieldCheck },
  { id: 'profile-audit', label: '布局审计', icon: ScrollText },
  { id: 'component-extension', label: 'Workspace 扩展', icon: Blocks },
  { id: 'catalog', label: 'Catalog', icon: Package },
  { id: 'installed', label: 'Installed', icon: Download },
  { id: 'slots', label: 'Slots', icon: PanelTop },
  { id: 'skills', label: 'Skills', icon: Sparkles },
  { id: 'mcp', label: 'MCP', icon: Network },
  { id: 'sandbox', label: 'Sandbox', icon: SquareDashed },
  { id: 'local-adapters', label: 'Local Adapters', icon: AppWindow },
  { id: 'permissions', label: 'Permissions', icon: PlugZap },
  { id: 'health', label: 'Health', icon: CircleGauge },
  { id: 'component-review', label: '组件审阅', icon: ShieldCheck },
  { id: 'component-audit', label: '组件审计', icon: ScrollText },
  { id: 'recovery', label: 'Recovery', icon: Wrench },
] as const satisfies readonly {
  readonly id: SettingsSection
  readonly label: string
  readonly icon: typeof Monitor
}[]

function slotStatus(slot: DesktopWorkspaceSlotCatalogItem): string {
  if (slot.posture === 'required') return '必需'
  if (slot.posture === 'unavailable') return '不可用'
  return '可配置'
}

function capabilityStatus(
  slot: DesktopWorkspaceSlotCatalogItem,
  enabled: boolean,
  workspaceFilesAuthorized: boolean,
  bridgeSubscribed: boolean,
): string {
  if (slot.posture === 'unavailable') return '不可用'
  if (!enabled) return '已关闭'
  if (slot.id === 'workspace.explorer') {
    return workspaceFilesAuthorized ? '已授权' : '待授权'
  }
  if (slot.id === 'event.output' || slot.id === 'event.agent-log') {
    return bridgeSubscribed ? '已订阅' : '未连接'
  }
  return '可用'
}

function auditEventLabel(event: DesktopWorkspaceCompositionAuditEvent): string {
  switch (event.eventType) {
    case 'workspace_composition_proposed':
      return '创建提案'
    case 'workspace_composition_rejected':
      return '拒绝提案'
    case 'workspace_composition_applied':
      return '应用修订'
  }
}

function auditEventSummary(event: DesktopWorkspaceCompositionAuditEvent): string {
  switch (event.eventType) {
    case 'workspace_composition_proposed':
      return `${event.payload.sourceKind} · base r${event.payload.baseRevision} · ${event.payload.proposalId}`
    case 'workspace_composition_rejected':
      return `${event.payload.proposalId} · ${event.payload.requestSha256}`
    case 'workspace_composition_applied':
      return `r${event.payload.revision} · ${event.payload.sourceKind} · ${event.payload.profileSha256}`
  }
}

function ProposalDiff({
  proposal,
  base,
}: {
  readonly proposal: DesktopWorkspaceCompositionProposal
  readonly base: DesktopWorkspaceCompositionProfileValue
}) {
  const rows = p7CompositionDiff(base, proposal.desiredProfile)
  return (
    <div className="p7-proposal-diff">
      {rows.length === 0 && <span className="p7-faint-text">无呈现差异</span>}
      {rows.map((row) => (
        <div key={row.key} className="p7-diff-row">
          <span>{row.label}</span>
          <del>{row.before}</del>
          <span aria-hidden="true">→</span>
          <ins>{row.after}</ins>
        </div>
      ))}
    </div>
  )
}

export function P7SettingsCenter(
  props: P7SettingsCenterProps & {
    readonly onClose: () => void
    readonly componentSurface: P7WorkspaceComponentSurfaceProjection
  },
) {
  const [section, setSection] = useState<SettingsSection>('appearance')
  const settingsId = useId().replaceAll(':', '')
  const settingsPanelId = `${settingsId}-panel`
  const navButtonsRef = useRef(new Map<SettingsSection, HTMLButtonElement>())
  const settingsPanelRef = useRef<HTMLElement>(null)
  const restoreFocusRef = useRef<HTMLElement | null>(null)
  const focusPanelAfterSectionRef = useRef(false)
  const preference = props.applicationPreference
  const snapshot = props.compositionSnapshot
  const draft = props.compositionDraft
  const current = snapshot?.profile.value ?? null
  const pending = snapshot?.proposals.filter((proposal) => proposal.decision === null) ?? []
  const catalog = snapshot?.slotCatalog ?? []
  const componentPending =
    props.snapshot?.proposals.filter((proposal) => proposal.decision === null) ?? []
  const providerSettingsEnabled = current?.slots['provider.settings'] === true
  const componentExtensionVisible = props.componentSurface.status === 'ready'
  const sections = SECTIONS.filter(
    (item) =>
      (item.id !== 'providers' || providerSettingsEnabled) &&
      (item.id !== 'component-extension' || componentExtensionVisible),
  )

  useEffect(() => {
    restoreFocusRef.current =
      typeof document !== 'undefined' && document.activeElement instanceof HTMLElement
        ? document.activeElement
        : null
    navButtonsRef.current.get('appearance')?.focus()
    return () => {
      const restore = restoreFocusRef.current
      if (restore?.isConnected) restore.focus()
    }
  }, [])

  useEffect(() => {
    if (!focusPanelAfterSectionRef.current) return
    focusPanelAfterSectionRef.current = false
    settingsPanelRef.current?.focus()
  }, [section])

  useEffect(() => {
    if (section === 'providers' && !providerSettingsEnabled) {
      setSection('workspace')
    }
  }, [providerSettingsEnabled, section])

  useEffect(() => {
    if (componentExtensionVisible) {
      setSection('component-extension')
    } else if (section === 'component-extension') {
      setSection('workspace')
    }
  }, [componentExtensionVisible, props.componentSurface.surface?.operationId, section])

  const patchDraft = (patch: Parameters<typeof p7PatchCompositionProfile>[1]) => {
    if (draft === null || props.compositionBusy) return
    props.onCompositionDraftChange(p7PatchCompositionProfile(draft, patch))
  }

  const toggleSlot = (slot: DesktopWorkspaceSlotCatalogItem, enabled: boolean) => {
    if (draft === null || props.compositionBusy || slot.posture !== 'admitted') return
    const layout: {
      agentPanel?: DesktopWorkspaceCompositionProfileValue['layout']['agentPanel']
      bottomPanel?: DesktopWorkspaceCompositionProfileValue['layout']['bottomPanel']
      sidebar?: DesktopWorkspaceCompositionProfileValue['layout']['sidebar']
    } = {}
    if (slot.id === 'agent.rail' && !enabled) layout.agentPanel = 'closed'
    if (slot.id === 'workspace.explorer' && !enabled && draft.layout.sidebar === 'explorer') {
      layout.sidebar = 'hidden'
    }
    if (slot.id === 'run.history' && !enabled && draft.layout.sidebar === 'run') {
      layout.sidebar = 'hidden'
    }
    if (slot.id === 'workspace.brief' && !enabled && draft.layout.sidebar === 'blackboard') {
      layout.sidebar = 'hidden'
    }
    if (slot.id === 'event.output' && !enabled && draft.layout.bottomPanel === 'output') {
      layout.bottomPanel = 'hidden'
    }
    if (slot.id === 'event.agent-log' && !enabled && draft.layout.bottomPanel === 'agent-log') {
      layout.bottomPanel = 'hidden'
    }
    patchDraft({ slots: { [slot.id]: enabled }, layout })
  }

  const activateSection = (next: SettingsSection) => {
    if (next === section) {
      settingsPanelRef.current?.focus()
      return
    }
    focusPanelAfterSectionRef.current = true
    setSection(next)
  }

  const moveNavFocus = (event: React.KeyboardEvent<HTMLButtonElement>, currentIndex: number) => {
    const key = event.key as P7SettingsNavigationKey
    const nextIndex = p7SettingsNavigationTargetIndex(currentIndex, sections.length, key)
    if (nextIndex === null) return
    event.preventDefault()
    navButtonsRef.current.get(sections[nextIndex]!.id)?.focus()
  }

  return (
    <div
      className="p7-settings-center"
      onKeyDown={(event) => {
        if (event.key !== 'Escape' || event.defaultPrevented) return
        event.preventDefault()
        props.onClose()
      }}
    >
      <aside className="p7-settings-nav" aria-label="设置分类">
        <div className="p7-settings-nav-head">
          <span>设置</span>
          <button type="button" aria-label="关闭设置" title="关闭设置" onClick={props.onClose}>
            <X size={15} />
          </button>
        </div>
        {sections.map(({ id, label, icon: Icon }, index) => (
          <button
            key={id}
            id={`${settingsId}-nav-${id}`}
            ref={(element) => {
              if (element === null) navButtonsRef.current.delete(id)
              else navButtonsRef.current.set(id, element)
            }}
            type="button"
            className="p7-settings-nav-item"
            aria-current={section === id ? 'page' : undefined}
            aria-controls={settingsPanelId}
            tabIndex={section === id ? 0 : -1}
            onKeyDown={(event) => moveNavFocus(event, index)}
            onClick={() => activateSection(id)}
          >
            <Icon size={15} />
            <span>{label}</span>
            {id === 'profile-review' && pending.length > 0 && (
              <span className="p7-settings-count">{pending.length}</span>
            )}
            {id === 'component-review' && componentPending.length > 0 && (
              <span className="p7-settings-count">{componentPending.length}</span>
            )}
          </button>
        ))}
        <div className="p7-settings-nav-foot">
          <span>{props.workspaceName}</span>
          <span>{snapshot === null ? '—' : `Revision ${snapshot.profile.revision}`}</span>
        </div>
      </aside>

      <main
        id={settingsPanelId}
        ref={settingsPanelRef}
        className="p7-settings-main"
        role="region"
        aria-labelledby={`${settingsId}-nav-${section}`}
        tabIndex={0}
      >
        {props.compositionNotice !== null && (
          <div className="p7-settings-notice p7-settings-global-notice" role="status">
            {props.compositionNotice}
          </div>
        )}
        {props.notice !== null && (
          <div className="p7-settings-notice p7-settings-global-notice" role="status">
            {props.notice}
          </div>
        )}
        {section === 'appearance' && (
          <section className="p7-settings-section">
            <header>
              <h1>外观</h1>
              <span>应用</span>
            </header>
            {props.applicationPreferenceStatus === 'loading' && (
              <div className="p7-settings-empty">正在读取…</div>
            )}
            {preference !== null && (
              <div className="p7-settings-group">
                <SettingRow label="界面密度">
                  <Segmented
                    value={preference.density}
                    options={[
                      { value: 'compact', label: '紧凑' },
                      { value: 'comfortable', label: '舒适' },
                    ]}
                    onChange={(density) =>
                      props.onApplicationPreferenceChange(density, preference.reduceMotion)
                    }
                  />
                </SettingRow>
                <SettingRow label="减少动画">
                  <Toggle
                    label="减少动画"
                    checked={preference.reduceMotion}
                    onChange={(reduceMotion) =>
                      props.onApplicationPreferenceChange(preference.density, reduceMotion)
                    }
                  />
                </SettingRow>
              </div>
            )}
          </section>
        )}

        {section === 'workspace' && (
          <section className="p7-settings-section" aria-busy={props.compositionBusy}>
            <header>
              <h1>{props.workspaceName}</h1>
              <span>standard-workbench v1</span>
            </header>
            {props.compositionStatus === 'loading' && (
              <div className="p7-settings-empty">正在读取 Workspace Profile…</div>
            )}
            {draft !== null && current !== null && (
              <>
                <div className="p7-settings-group">
                  <SettingRow label="密度覆盖">
                    <Segmented
                      value={draft.appearance.density}
                      disabled={props.compositionBusy}
                      options={[
                        { value: 'inherit', label: '跟随应用' },
                        { value: 'compact', label: '紧凑' },
                        { value: 'comfortable', label: '舒适' },
                      ]}
                      onChange={(density) => patchDraft({ appearance: { density } })}
                    />
                  </SettingRow>
                  <SettingRow label="安静界面">
                    <Toggle
                      label="安静界面"
                      checked={draft.appearance.quietChrome}
                      disabled={props.compositionBusy}
                      onChange={(quietChrome) => patchDraft({ appearance: { quietChrome } })}
                    />
                  </SettingRow>
                  <SettingRow label="主侧栏" labelFor={`${settingsId}-workspace-sidebar`}>
                    <select
                      id={`${settingsId}-workspace-sidebar`}
                      value={draft.layout.sidebar}
                      disabled={props.compositionBusy}
                      onChange={(event) =>
                        patchDraft({
                          layout: {
                            sidebar: event.target.value as typeof draft.layout.sidebar,
                          },
                        })
                      }
                    >
                      <option
                        value="explorer"
                        disabled={
                          !p7CompositionLayoutChoiceEnabled(draft, {
                            field: 'sidebar',
                            value: 'explorer',
                          })
                        }
                      >
                        资源管理器
                      </option>
                      <option
                        value="run"
                        disabled={
                          !p7CompositionLayoutChoiceEnabled(draft, {
                            field: 'sidebar',
                            value: 'run',
                          })
                        }
                      >
                        运行
                      </option>
                      <option
                        value="blackboard"
                        disabled={
                          !p7CompositionLayoutChoiceEnabled(draft, {
                            field: 'sidebar',
                            value: 'blackboard',
                          })
                        }
                      >
                        黑板
                      </option>
                      <option value="hidden">隐藏</option>
                    </select>
                  </SettingRow>
                  <SettingRow label="Agent 面板">
                    <Segmented
                      value={draft.layout.agentPanel}
                      disabled={props.compositionBusy}
                      options={[
                        {
                          value: 'open',
                          label: '打开',
                          disabled: !p7CompositionLayoutChoiceEnabled(draft, {
                            field: 'agentPanel',
                            value: 'open',
                          }),
                        },
                        { value: 'closed', label: '关闭' },
                      ]}
                      onChange={(agentPanel) => patchDraft({ layout: { agentPanel } })}
                    />
                  </SettingRow>
                  <SettingRow label="底部面板" labelFor={`${settingsId}-workspace-bottom-panel`}>
                    <select
                      id={`${settingsId}-workspace-bottom-panel`}
                      value={draft.layout.bottomPanel}
                      disabled={props.compositionBusy}
                      onChange={(event) =>
                        patchDraft({
                          layout: {
                            bottomPanel: event.target.value as typeof draft.layout.bottomPanel,
                          },
                        })
                      }
                    >
                      <option value="hidden">隐藏</option>
                      <option
                        value="output"
                        disabled={
                          !p7CompositionLayoutChoiceEnabled(draft, {
                            field: 'bottomPanel',
                            value: 'output',
                          })
                        }
                      >
                        输出
                      </option>
                      <option
                        value="agent-log"
                        disabled={
                          !p7CompositionLayoutChoiceEnabled(draft, {
                            field: 'bottomPanel',
                            value: 'agent-log',
                          })
                        }
                      >
                        Agent Log
                      </option>
                    </select>
                  </SettingRow>
                  <SettingRow label="专注模式">
                    <Toggle
                      label="专注模式"
                      checked={draft.layout.focusMode}
                      disabled={props.compositionBusy}
                      onChange={(focusMode) => patchDraft({ layout: { focusMode } })}
                    />
                  </SettingRow>
                </div>

                <div className="p7-settings-actions">
                  <span>{p7CompositionDiff(current, draft).length} 项变更</span>
                  <button
                    type="button"
                    className="p7-settings-primary"
                    disabled={
                      props.compositionBusy || p7CompositionDiff(current, draft).length === 0
                    }
                    onClick={props.onCreateCompositionProposal}
                  >
                    <Save size={14} />
                    创建提案
                  </button>
                </div>

                <div className="p7-settings-ai">
                  <Bot size={16} />
                  <input
                    value={props.compositionIntent}
                    maxLength={2_000}
                    placeholder="让 Agent 调整当前 Workspace…"
                    onChange={(event) => props.onCompositionIntentChange(event.target.value)}
                  />
                  <button
                    type="button"
                    disabled={props.compositionBusy || props.compositionIntent.trim() === ''}
                    onClick={props.onRequestAssistantComposition}
                    title="生成 Agent 提案"
                  >
                    <Sparkles size={14} />
                    生成提案
                  </button>
                </div>
              </>
            )}
          </section>
        )}

        {section === 'providers' && providerSettingsEnabled && (
          <section className="p7-settings-section">
            <header>
              <h1>Provider</h1>
              <span>应用</span>
            </header>
            <form
              className="p7-provider-grid"
              onSubmit={(event) => {
                event.preventDefault()
                props.onSaveProvider()
              }}
            >
              <label>
                <span>名称</span>
                <input
                  value={props.providerForm.displayName}
                  onChange={(event) =>
                    props.onProviderFormChange({ displayName: event.target.value })
                  }
                />
              </label>
              <label className="p7-provider-wide">
                <span>Base URL</span>
                <input
                  value={props.providerForm.baseUrl}
                  onChange={(event) => props.onProviderFormChange({ baseUrl: event.target.value })}
                />
              </label>
              <label className="p7-provider-wide">
                <span>API Key</span>
                <input
                  type="password"
                  autoComplete="off"
                  value={props.providerForm.apiKey}
                  onChange={(event) => props.onProviderFormChange({ apiKey: event.target.value })}
                />
              </label>
              <label>
                <span>模型</span>
                <input
                  value={props.providerForm.modelName}
                  onChange={(event) =>
                    props.onProviderFormChange({ modelName: event.target.value })
                  }
                />
              </label>
              <label>
                <span>档位</span>
                <select
                  value={props.providerForm.gear}
                  onChange={(event) =>
                    props.onProviderFormChange({ gear: event.target.value as DesktopReasoningGear })
                  }
                >
                  <option value="economy">经济</option>
                  <option value="standard">标准</option>
                  <option value="deep">深度</option>
                  <option value="audit">审计</option>
                </select>
              </label>
              <label>
                <span>思考深度</span>
                <select
                  value={props.providerForm.thinkingDepth}
                  onChange={(event) =>
                    props.onProviderFormChange({
                      thinkingDepth: event.target.value as DesktopThinkingDepth,
                    })
                  }
                >
                  <option value="disabled">关闭</option>
                  <option value="low">低</option>
                  <option value="medium">中</option>
                  <option value="high">高</option>
                </select>
              </label>
              <div className="p7-provider-toggles">
                <Toggle
                  label="启用"
                  checked={props.providerForm.isEnabled}
                  onChange={(isEnabled) => props.onProviderFormChange({ isEnabled })}
                />
                <Toggle
                  label="默认"
                  checked={props.providerForm.isDefault}
                  onChange={(isDefault) => props.onProviderFormChange({ isDefault })}
                />
                <Toggle
                  label="本机 HTTP"
                  checked={props.providerForm.allowLoopbackHttp}
                  onChange={(allowLoopbackHttp) =>
                    props.onProviderFormChange({ allowLoopbackHttp })
                  }
                />
              </div>
              <button type="submit" className="p7-settings-primary" disabled={props.submitting}>
                <Save size={14} />
                {props.submitting ? '保存中…' : '保存'}
              </button>
            </form>
            {props.testResult !== null && (
              <div className="p7-settings-notice">{props.testResult}</div>
            )}
            <div className="p7-provider-list">
              {props.providers.map((provider) => (
                <div key={provider.id} className="p7-provider-row">
                  <span className="p7-dot p7-dot-green" />
                  <strong>{provider.displayName}</strong>
                  <span>{provider.modelName}</span>
                  <span>{provider.family}</span>
                  <button type="button" onClick={() => props.onTestProvider(provider.id)}>
                    测试
                  </button>
                </div>
              ))}
              {props.providers.length === 0 && (
                <div className="p7-settings-empty">暂无 Provider</div>
              )}
            </div>
          </section>
        )}

        {section === 'capabilities' && (
          <section className="p7-settings-section">
            <header>
              <h1>能力边界</h1>
              <span>当前 Workspace</span>
            </header>
            {snapshot === null && (
              <div className="p7-settings-empty">
                {props.compositionStatus === 'error' ? 'Profile 读取失败' : '正在读取…'}
              </div>
            )}
            <div className="p7-slot-list">
              {catalog.map((slot) => {
                const enabled = current?.slots[slot.id] ?? false
                return (
                  <div key={slot.id} className="p7-slot-row p7-slot-row-readonly">
                    <span className={`p7-slot-state p7-slot-${slot.posture}`} />
                    <div>
                      <strong>{slot.label}</strong>
                      <span>{slot.id}</span>
                    </div>
                    <span className="p7-slot-region">{slot.region}</span>
                    <span className="p7-slot-posture">{slotStatus(slot)}</span>
                    <span className="p7-capability-live">
                      {capabilityStatus(
                        slot,
                        enabled,
                        props.workspaceFilesAuthorized,
                        props.bridgeSubscribed,
                      )}
                    </span>
                  </div>
                )
              })}
            </div>
          </section>
        )}

        {section === 'layout-components' && (
          <section className="p7-settings-section">
            <header>
              <h1>组件</h1>
              <span>当前 Workspace</span>
            </header>
            <div className="p7-slot-list">
              {catalog.map((slot) => (
                <div key={slot.id} className="p7-slot-row">
                  <span className={`p7-slot-state p7-slot-${slot.posture}`} />
                  <div>
                    <strong>{slot.label}</strong>
                    <span>{slot.id}</span>
                  </div>
                  <span className="p7-slot-region">{slot.region}</span>
                  <span className="p7-slot-posture">{slotStatus(slot)}</span>
                  <Toggle
                    label={slot.label}
                    checked={draft?.slots[slot.id] ?? false}
                    disabled={
                      props.compositionBusy || slot.posture !== 'admitted' || draft === null
                    }
                    onChange={(enabled) => toggleSlot(slot, enabled)}
                  />
                </div>
              ))}
            </div>
          </section>
        )}

        {section === 'profile-review' && (
          <section className="p7-settings-section">
            <header>
              <h1>审阅</h1>
              <span>{pending.length} 个待决提案</span>
            </header>
            {snapshot !== null &&
              pending.map((proposal) => {
                const review = p7CompositionProposalReview(snapshot, proposal)
                return (
                  <article key={proposal.id} className="p7-proposal">
                    <div className="p7-proposal-head">
                      <div>
                        <strong>
                          {proposal.sourceKind === 'assistant'
                            ? 'Agent 提案'
                            : proposal.sourceKind === 'rollback'
                              ? '回滚提案'
                              : 'Owner 提案'}
                        </strong>
                        <code className="p7-proposal-digest" title={proposal.requestSha256}>
                          {proposal.requestSha256}
                        </code>
                      </div>
                      <span>
                        {review.approvable ? `Revision ${proposal.baseRevision}` : '基线已过期'}
                      </span>
                    </div>
                    {review.base === null ? (
                      <div className="p7-settings-empty">提案基线不在当前历史投影中</div>
                    ) : (
                      <ProposalDiff proposal={proposal} base={review.base.value} />
                    )}
                    <div className="p7-proposal-actions">
                      <button
                        type="button"
                        disabled={props.compositionBusy}
                        onClick={() => props.onDecideCompositionProposal(proposal, 'reject')}
                      >
                        <X size={14} />
                        拒绝
                      </button>
                      <button
                        type="button"
                        className="p7-settings-primary"
                        disabled={props.compositionBusy || !review.approvable}
                        title={review.approvable ? '批准精确请求摘要' : '提案基线已过期，只能拒绝'}
                        onClick={() => props.onDecideCompositionProposal(proposal, 'approve')}
                      >
                        <Check size={14} />
                        批准
                      </button>
                    </div>
                  </article>
                )
              })}
            {pending.length === 0 && <div className="p7-settings-empty">没有待决提案</div>}

            <h2 className="p7-settings-subtitle">Revision</h2>
            <div className="p7-revision-list">
              {(snapshot?.revisions ?? []).map((revision) => (
                <div key={revision.revision} className="p7-revision-row">
                  <span>r{revision.revision}</span>
                  <strong>{revision.sourceKind}</strong>
                  <code>{revision.profileSha256.slice(0, 12)}</code>
                  {revision.revision === snapshot?.profile.revision ? (
                    <span className="p7-revision-current">当前</span>
                  ) : (
                    <button
                      type="button"
                      disabled={props.compositionBusy}
                      onClick={() => props.onProposeCompositionRollback(revision.revision)}
                    >
                      <RotateCcw size={13} />
                      回滚
                    </button>
                  )}
                </div>
              ))}
            </div>
          </section>
        )}

        {section === 'profile-audit' && (
          <section className="p7-settings-section">
            <header>
              <h1>审计</h1>
              <span>Append-only</span>
            </header>
            <div className="p7-audit-list">
              {(snapshot?.audit ?? []).map((event) => {
                const summary = auditEventSummary(event)
                return (
                  <div key={event.sequence} className="p7-audit-row">
                    <span className="p7-audit-sequence">#{event.sequence}</span>
                    <div>
                      <strong>{auditEventLabel(event)}</strong>
                      <code title={summary}>{summary}</code>
                    </div>
                    <time>{event.createdAt}</time>
                  </div>
                )
              })}
              {snapshot !== null && snapshot.audit.length === 0 && (
                <div className="p7-settings-empty">暂无 Workspace Profile 审计记录</div>
              )}
              {snapshot === null && (
                <div className="p7-settings-empty">
                  {props.compositionStatus === 'error' ? '审计读取失败' : '正在读取审计…'}
                </div>
              )}
            </div>
          </section>
        )}
        {section === 'component-extension' && componentExtensionVisible && (
          <section className="p7-settings-section p7-component-settings-slot">
            <header>
              <h1>Workspace 扩展</h1>
              <span>宿主声明式渲染</span>
            </header>
            <P7ComponentSurface projection={props.componentSurface} />
          </section>
        )}
        {(
          [
            'catalog',
            'installed',
            'slots',
            'skills',
            'mcp',
            'sandbox',
            'local-adapters',
            'permissions',
            'health',
            'component-review',
            'component-audit',
            'recovery',
          ] as const satisfies readonly P7WorkspaceComponentSettingsSection[]
        ).includes(section as P7WorkspaceComponentSettingsSection) && (
          <P7WorkspaceComponentView
            section={section as P7WorkspaceComponentSettingsSection}
            {...props}
          />
        )}
      </main>
    </div>
  )
}
