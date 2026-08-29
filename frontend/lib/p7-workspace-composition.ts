import type {
  DesktopApplicationPreference,
  DesktopMessage,
  DesktopWorkbenchDensity,
  DesktopWorkspaceCompositionProfileValue,
  DesktopWorkspaceCompositionProposal,
  DesktopWorkspaceCompositionRevision,
  DesktopWorkspaceCompositionSnapshot,
  DesktopWorkspaceSlotId,
} from './desktop-bridge'

export const P7_COMPOSITION_SLOT_IDS = Object.freeze([
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
] as const satisfies readonly DesktopWorkspaceSlotId[])
const P7_REQUIRED_SLOTS = Object.freeze([
  'conversation.transcript',
  'settings.center',
  'workspace.explorer',
] as const)
const P7_UNAVAILABLE_SLOTS = Object.freeze([
  'knowledge.ebook',
  'mcp.catalog',
  'sandbox.runtime',
  'skills.catalog',
  'source-control',
  'terminal',
] as const)

export type P7CompositionLoadStatus = 'idle' | 'loading' | 'ready' | 'error'

export interface P7CompositionProjection {
  readonly status: P7CompositionLoadStatus
  readonly snapshot: DesktopWorkspaceCompositionSnapshot | null
}

export interface P7CompositionDiffRow {
  readonly key: string
  readonly label: string
  readonly before: string
  readonly after: string
}

export interface P7CompositionProposalReview {
  readonly base: DesktopWorkspaceCompositionRevision | null
  readonly approvable: boolean
}

function exactKeys(value: Record<string, unknown>, expected: readonly string[]): boolean {
  const actual = Object.keys(value).sort()
  const wanted = [...expected].sort()
  return actual.length === wanted.length && actual.every((key, index) => key === wanted[index])
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

export function p7CompositionProjection(input: {
  readonly loadedWorkspaceId: string | null
  readonly viewWorkspaceId: string | null
  readonly status: P7CompositionLoadStatus
  readonly snapshot: DesktopWorkspaceCompositionSnapshot | null
}): P7CompositionProjection {
  if (input.viewWorkspaceId === null || input.loadedWorkspaceId !== input.viewWorkspaceId) {
    return Object.freeze({
      status: input.viewWorkspaceId === null ? 'idle' : 'loading',
      snapshot: null,
    })
  }
  if (input.status !== 'ready') {
    return Object.freeze({ status: input.status, snapshot: null })
  }
  if (input.snapshot?.profile.workspaceId !== input.viewWorkspaceId) {
    return Object.freeze({ status: 'error', snapshot: null })
  }
  return Object.freeze({
    status: 'ready',
    snapshot: input.snapshot,
  })
}

export function p7WorkspaceSelectionChangesScope(
  currentWorkspaceId: string | null,
  nextWorkspaceId: string,
): boolean {
  return currentWorkspaceId !== nextWorkspaceId
}

export function p7EffectiveDensity(
  preference: DesktopApplicationPreference | null,
  profile: DesktopWorkspaceCompositionProfileValue | null,
): DesktopWorkbenchDensity {
  const workspaceDensity = profile?.appearance.density
  if (workspaceDensity === 'compact' || workspaceDensity === 'comfortable') {
    return workspaceDensity
  }
  return preference?.density ?? 'compact'
}

export function p7CompositionSlotEnabled(
  profile: DesktopWorkspaceCompositionProfileValue | null,
  slotId: DesktopWorkspaceSlotId,
): boolean {
  if (profile !== null) return profile.slots[slotId]
  return P7_REQUIRED_SLOTS.some((required) => required === slotId)
}

export function p7CloneCompositionProfile(
  profile: DesktopWorkspaceCompositionProfileValue,
): DesktopWorkspaceCompositionProfileValue {
  return Object.freeze({
    schemaVersion: 1,
    template: Object.freeze({ id: 'standard-workbench', version: 1 }),
    appearance: Object.freeze({ ...profile.appearance }),
    layout: Object.freeze({ ...profile.layout }),
    slots: Object.freeze(
      Object.fromEntries(
        P7_COMPOSITION_SLOT_IDS.map((slotId) => [slotId, profile.slots[slotId]]),
      ) as Record<DesktopWorkspaceSlotId, boolean>,
    ),
  })
}

export function p7PatchCompositionProfile(
  profile: DesktopWorkspaceCompositionProfileValue,
  patch: Readonly<{
    appearance?: Partial<DesktopWorkspaceCompositionProfileValue['appearance']>
    layout?: Partial<DesktopWorkspaceCompositionProfileValue['layout']>
    slots?: Partial<Record<DesktopWorkspaceSlotId, boolean>>
  }>,
): DesktopWorkspaceCompositionProfileValue {
  return p7CloneCompositionProfile({
    ...profile,
    appearance: { ...profile.appearance, ...patch.appearance },
    layout: { ...profile.layout, ...patch.layout },
    slots: { ...profile.slots, ...patch.slots },
  })
}

type P7CompositionLayoutChoice =
  | Readonly<{
      field: 'sidebar'
      value: DesktopWorkspaceCompositionProfileValue['layout']['sidebar']
    }>
  | Readonly<{
      field: 'agentPanel'
      value: DesktopWorkspaceCompositionProfileValue['layout']['agentPanel']
    }>
  | Readonly<{
      field: 'bottomPanel'
      value: DesktopWorkspaceCompositionProfileValue['layout']['bottomPanel']
    }>

export function p7CompositionLayoutChoiceEnabled(
  profile: DesktopWorkspaceCompositionProfileValue,
  choice: P7CompositionLayoutChoice,
): boolean {
  if (choice.field === 'sidebar') {
    if (choice.value === 'explorer') return profile.slots['workspace.explorer']
    if (choice.value === 'run') return profile.slots['run.history']
    if (choice.value === 'blackboard') return profile.slots['workspace.brief']
    return true
  }
  if (choice.field === 'agentPanel') {
    return choice.value === 'closed' || profile.slots['agent.rail']
  }
  if (choice.value === 'output') return profile.slots['event.output']
  if (choice.value === 'agent-log') return profile.slots['event.agent-log']
  return true
}

const FIELD_LABELS = Object.freeze({
  'appearance.density': '界面密度',
  'appearance.quietChrome': '安静界面',
  'layout.agentPanel': 'Agent 面板',
  'layout.bottomPanel': '底部面板',
  'layout.focusMode': '专注模式',
  'layout.sidebar': '主侧栏',
} as const)

function textValue(value: string | boolean): string {
  if (typeof value === 'boolean') return value ? '开启' : '关闭'
  return value
}

export function p7CompositionDiff(
  current: DesktopWorkspaceCompositionProfileValue,
  desired: DesktopWorkspaceCompositionProfileValue,
): readonly P7CompositionDiffRow[] {
  const fields = [
    ['appearance.density', current.appearance.density, desired.appearance.density],
    ['appearance.quietChrome', current.appearance.quietChrome, desired.appearance.quietChrome],
    ['layout.agentPanel', current.layout.agentPanel, desired.layout.agentPanel],
    ['layout.bottomPanel', current.layout.bottomPanel, desired.layout.bottomPanel],
    ['layout.focusMode', current.layout.focusMode, desired.layout.focusMode],
    ['layout.sidebar', current.layout.sidebar, desired.layout.sidebar],
  ] as const
  const rows: P7CompositionDiffRow[] = []
  for (const [key, before, after] of fields) {
    if (before === after) continue
    rows.push(
      Object.freeze({
        key,
        label: FIELD_LABELS[key],
        before: textValue(before),
        after: textValue(after),
      }),
    )
  }
  for (const slotId of P7_COMPOSITION_SLOT_IDS) {
    if (current.slots[slotId] === desired.slots[slotId]) continue
    rows.push(
      Object.freeze({
        key: `slots.${slotId}`,
        label: slotId,
        before: textValue(current.slots[slotId]),
        after: textValue(desired.slots[slotId]),
      }),
    )
  }
  return Object.freeze(rows)
}

export function p7CompositionProposalReview(
  snapshot: DesktopWorkspaceCompositionSnapshot,
  proposal: DesktopWorkspaceCompositionProposal,
): P7CompositionProposalReview {
  const base =
    snapshot.revisions.find(
      (revision) =>
        revision.workspaceId === proposal.workspaceId &&
        revision.revision === proposal.baseRevision &&
        revision.profileSha256 === proposal.baseProfileSha256,
    ) ?? null
  return Object.freeze({
    base,
    approvable:
      proposal.workspaceId === snapshot.profile.workspaceId &&
      base !== null &&
      proposal.baseRevision === snapshot.profile.revision &&
      proposal.baseProfileSha256 === snapshot.profile.profileSha256 &&
      proposal.decision === null,
  })
}

export function p7ProfilePayload(profile: DesktopWorkspaceCompositionProfileValue) {
  return Object.freeze({
    schema_version: 1,
    template: Object.freeze({ id: 'standard-workbench', version: 1 }),
    appearance: Object.freeze({
      density: profile.appearance.density,
      quiet_chrome: profile.appearance.quietChrome,
    }),
    layout: Object.freeze({
      agent_panel: profile.layout.agentPanel,
      bottom_panel: profile.layout.bottomPanel,
      focus_mode: profile.layout.focusMode,
      sidebar: profile.layout.sidebar,
    }),
    slots: Object.freeze(
      Object.fromEntries(P7_COMPOSITION_SLOT_IDS.map((slotId) => [slotId, profile.slots[slotId]])),
    ),
  })
}

export function p7CompositionAssistantPrompt(
  intent: string,
  current: DesktopWorkspaceCompositionProfileValue,
): string | null {
  const normalized = intent.trim()
  if (normalized.length < 1 || normalized.length > 2_000) return null
  return [
    '你正在为当前 OmniBase Workspace 生成一个仅影响呈现层的工作台组合提案。',
    '不得请求、安装或启用插件、MCP、Skill、沙箱、终端、源码管理或任何新 capability。',
    '不得改变 standard-workbench 模板标识或版本；required/unavailable Slot 必须保持原值。',
    `Owner 意图：${normalized}`,
    `当前 Profile：${JSON.stringify(p7ProfilePayload(current))}`,
    '只输出一个 JSON 对象，不要 Markdown、解释或代码围栏。格式必须精确为：',
    '{"type":"omnibase.workspace-composition.proposal.v1","desired_profile":{...完整 Profile...}}',
  ].join('\n')
}

function parseProfilePayload(value: unknown): DesktopWorkspaceCompositionProfileValue | null {
  if (
    !isRecord(value) ||
    !exactKeys(value, ['appearance', 'layout', 'schema_version', 'slots', 'template']) ||
    value.schema_version !== 1 ||
    !isRecord(value.template) ||
    !exactKeys(value.template, ['id', 'version']) ||
    value.template.id !== 'standard-workbench' ||
    value.template.version !== 1 ||
    !isRecord(value.appearance) ||
    !exactKeys(value.appearance, ['density', 'quiet_chrome']) ||
    (value.appearance.density !== 'inherit' &&
      value.appearance.density !== 'compact' &&
      value.appearance.density !== 'comfortable') ||
    typeof value.appearance.quiet_chrome !== 'boolean' ||
    !isRecord(value.layout) ||
    !exactKeys(value.layout, ['agent_panel', 'bottom_panel', 'focus_mode', 'sidebar']) ||
    (value.layout.agent_panel !== 'open' && value.layout.agent_panel !== 'closed') ||
    (value.layout.bottom_panel !== 'hidden' &&
      value.layout.bottom_panel !== 'output' &&
      value.layout.bottom_panel !== 'agent-log') ||
    typeof value.layout.focus_mode !== 'boolean' ||
    (value.layout.sidebar !== 'explorer' &&
      value.layout.sidebar !== 'run' &&
      value.layout.sidebar !== 'blackboard' &&
      value.layout.sidebar !== 'hidden') ||
    !isRecord(value.slots) ||
    !exactKeys(value.slots, P7_COMPOSITION_SLOT_IDS) ||
    P7_COMPOSITION_SLOT_IDS.some(
      (slotId) => typeof (value.slots as Record<string, unknown>)[slotId] !== 'boolean',
    )
  ) {
    return null
  }
  const slots = value.slots as Record<DesktopWorkspaceSlotId, boolean>
  if (
    P7_REQUIRED_SLOTS.some((slotId) => !slots[slotId]) ||
    P7_UNAVAILABLE_SLOTS.some((slotId) => slots[slotId]) ||
    (!slots['agent.rail'] && value.layout.agent_panel !== 'closed') ||
    (!slots['workspace.explorer'] && value.layout.sidebar === 'explorer') ||
    (!slots['run.history'] && value.layout.sidebar === 'run') ||
    (!slots['workspace.brief'] && value.layout.sidebar === 'blackboard') ||
    (!slots['event.output'] && value.layout.bottom_panel === 'output') ||
    (!slots['event.agent-log'] && value.layout.bottom_panel === 'agent-log')
  ) {
    return null
  }
  return p7CloneCompositionProfile({
    schemaVersion: 1,
    template: { id: 'standard-workbench', version: 1 },
    appearance: {
      density: value.appearance.density,
      quietChrome: value.appearance.quiet_chrome,
    },
    layout: {
      agentPanel: value.layout.agent_panel,
      bottomPanel: value.layout.bottom_panel,
      focusMode: value.layout.focus_mode,
      sidebar: value.layout.sidebar,
    },
    slots: Object.fromEntries(
      P7_COMPOSITION_SLOT_IDS.map((slotId) => [slotId, slots[slotId]]),
    ) as Record<DesktopWorkspaceSlotId, boolean>,
  })
}

export function p7ParseAssistantCompositionEnvelope(
  content: string,
): DesktopWorkspaceCompositionProfileValue | null {
  if (content.length < 2 || content.length > 32_768) return null
  try {
    const value: unknown = JSON.parse(content)
    if (
      !isRecord(value) ||
      !exactKeys(value, ['desired_profile', 'type']) ||
      value.type !== 'omnibase.workspace-composition.proposal.v1'
    ) {
      return null
    }
    return parseProfilePayload(value.desired_profile)
  } catch {
    return null
  }
}

export function p7FindNewAssistantCompositionMessage(
  messages: readonly DesktopMessage[],
  previousMessageIds: ReadonlySet<string>,
): DesktopMessage | null {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const message = messages[index]
    if (
      message !== undefined &&
      !previousMessageIds.has(message.id) &&
      message.role === 'assistant' &&
      message.status === 'completed' &&
      message.invocationId !== null &&
      message.invocation?.id === message.invocationId &&
      message.invocation.status === 'succeeded' &&
      p7ParseAssistantCompositionEnvelope(message.content) !== null
    ) {
      return message
    }
  }
  return null
}
