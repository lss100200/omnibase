import type { AgentModelSettingRead, ProviderRuntimePosture } from '@/lib/types'

export const P6_READONLY_MCP_TOOLS = [
  {
    id: 'omnibase_files_list',
    label: '文件清单',
    boundary: '只列出显式授权根目录内的有界条目。',
  },
  {
    id: 'omnibase_files_read',
    label: '文本读取',
    boundary: '只读取授权根目录内的 UTF-8 文本，并拒绝敏感文件。',
  },
  {
    id: 'omnibase_git_inspect',
    label: 'Git 检查',
    boundary: '只允许 status/log 元数据操作，不写仓库。',
  },
] as const

export interface P6CapabilitySummary {
  readonly defaultRuntimeSource: ProviderRuntimePosture['credential_source']
  readonly roleTotal: number
  readonly readyRoles: number
  readonly pendingRoles: number
  readonly unavailableRoles: number
  readonly explicitOverrides: number
}

export function summarizeP6ModelCapabilities(
  posture: ProviderRuntimePosture,
  settings: readonly AgentModelSettingRead[],
): P6CapabilitySummary {
  const ready = settings.filter((item) => item.state === 'active' || item.state === 'inherited')
  return {
    defaultRuntimeSource: posture.credential_source,
    roleTotal: settings.length,
    readyRoles: ready.length,
    pendingRoles: settings.filter((item) => item.state === 'pending').length,
    unavailableRoles: settings.filter((item) => item.state === 'unavailable').length,
    explicitOverrides: settings.filter((item) => !item.inherit_default).length,
  }
}
