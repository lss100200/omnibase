import {
  desktopTeamStatusForRole,
  type DesktopTeamLiveState,
  type DesktopTeamNodeStatusText,
  type PersonalEmployeeId,
} from './desktop-team-lifecycle'

export const TEAM_ROLE_LABELS: Readonly<Record<PersonalEmployeeId, string>> = {
  parent: '父 Agent',
  product: '产品经理',
  ux: 'UI/UX',
  frontend: '前端工程师',
  backend: '后端工程师',
  data: '数据工程师',
  security: '安全架构师',
  qa: '测试工程师',
  operations: '运维工程师',
  docs: '文档工程师',
}

export const TEAM_ROLE_ORDER: readonly PersonalEmployeeId[] = [
  'parent',
  'product',
  'ux',
  'frontend',
  'backend',
  'data',
  'security',
  'qa',
  'operations',
  'docs',
]

export interface DesktopTeamEmployeeRow {
  readonly roleId: PersonalEmployeeId
  readonly label: string
  readonly statusText: DesktopTeamNodeStatusText
}

export function projectDesktopTeamEmployees(
  state: DesktopTeamLiveState,
): readonly DesktopTeamEmployeeRow[] {
  return TEAM_ROLE_ORDER.map((roleId) => ({
    roleId,
    label: TEAM_ROLE_LABELS[roleId],
    statusText: desktopTeamStatusForRole(state, roleId),
  }))
}

export function projectDesktopTeamBudget(state: DesktopTeamLiveState): string {
  return `已用 ${state.consumedProviderCalls} / 上限 ${state.maximumProviderCalls} 次调用`
}

export function projectDesktopTeamTimeline(
  state: DesktopTeamLiveState,
): DesktopTeamLiveState['nodes'] {
  return [...state.nodes].sort((left, right) => left.ordinal - right.ordinal)
}

export function desktopTeamTranscriptHighlight(
  state: DesktopTeamLiveState,
  viewingWorkspaceId: string | null,
  viewingConversationId: string | null,
): string | null {
  if (
    state.originWorkspaceId !== viewingWorkspaceId ||
    state.originConversationId !== viewingConversationId
  ) {
    return null
  }
  return state.parentFinalAnswer
}
