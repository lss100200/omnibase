export const P6_WORKBENCH_STORAGE_KEY = 'omnibase.p6.workbench.v1'
export const P6_WORKBENCH_SCHEMA_VERSION = 1 as const
export const P6_WORKBENCH_MAX_SESSIONS = 80
export const P6_WORKBENCH_MAX_MESSAGES_PER_SESSION = 400
export const P6_WORKBENCH_MAX_TIMELINE_EVENTS_PER_SESSION = 1_200
export const P6_WORKBENCH_MAX_MESSAGE_CHARACTERS = 32_000
export const P6_WORKBENCH_MAX_TIMELINE_LABEL_CHARACTERS = 512
export const P6_WORKBENCH_MAX_SESSION_BYTES = 768 * 1024
export const P6_WORKBENCH_MAX_STORE_BYTES = 4 * 1024 * 1024
export const P6_AGENT_ALPHA_MAX_MESSAGE_CHARACTERS = 32_000

const P6_WORKBENCH_MAX_ID_CHARACTERS = 200
const P6_WORKBENCH_MAX_WORKSPACE_ID_CHARACTERS = 200
const P6_WORKBENCH_MAX_DATE_CHARACTERS = 64
const PERSISTENCE_REDACTED_MARKER = '[OMNIBASE_LOCAL_REDACTED]'
const PERSISTENCE_TRUNCATED_MARKER = '\n[OMNIBASE_LOCAL_TRUNCATED]'

export type EmployeeId =
  | 'parent'
  | 'product'
  | 'ux'
  | 'frontend'
  | 'backend'
  | 'data'
  | 'security'
  | 'qa'
  | 'operations'
  | 'docs'

export type SpecialistEmployeeId = Exclude<EmployeeId, 'parent'>
export type EmployeeState = 'active' | 'dormant' | 'invoked' | 'working' | 'reporting'
export type WorkbenchMessageRole = 'user' | 'agent' | 'system'
export type WorkbenchTimelineKind =
  | 'session_created'
  | 'session_renamed'
  | 'session_pinned'
  | 'session_unpinned'
  | 'session_archived'
  | 'session_restored'
  | 'employee_invoked'
  | 'invocation_started'
  | 'invocation_completed'
  | 'invocation_cancelled'
  | 'invocation_failed'
  | 'invocation_interrupted_unknown'
  | 'employee_returned_dormant'
  | 'message_added'
  | 'history_compacted'

export interface EmployeeDefinition {
  readonly id: EmployeeId
  readonly displayName: string
  readonly shortName: string
  readonly aliases: readonly string[]
  readonly title: string
  readonly responsibility: string
  readonly boundary: string
  readonly defaultState: 'active' | 'dormant'
}

export interface WorkbenchMessage {
  readonly id: string
  readonly role: WorkbenchMessageRole
  readonly employeeId: EmployeeId | null
  readonly content: string
  readonly createdAt: string
}

export interface WorkbenchTimelineEvent {
  readonly id: string
  readonly kind: WorkbenchTimelineKind
  readonly label: string
  readonly createdAt: string
  readonly employeeId?: EmployeeId
}

export interface WorkbenchSession {
  readonly id: string
  readonly title: string
  readonly workspaceId: string | null
  readonly createdAt: string
  readonly updatedAt: string
  readonly pinned: boolean
  readonly archivedAt: string | null
  readonly messages: readonly WorkbenchMessage[]
  readonly timeline: readonly WorkbenchTimelineEvent[]
}

export interface WorkbenchState {
  readonly schemaVersion: typeof P6_WORKBENCH_SCHEMA_VERSION
  readonly activeSessionId: string
  readonly sessions: readonly WorkbenchSession[]
}

export type EmployeeInvocation =
  | {
      readonly ok: true
      readonly employee: EmployeeDefinition
      readonly message: string
      readonly explicitMention: boolean
    }
  | {
      readonly ok: false
      readonly code:
        | 'empty_message'
        | 'unknown_employee'
        | 'multiple_employees'
        | 'invalid_mention'
        | 'broadcast_employee'
      readonly message: string
    }

export type WorkbenchSessionAddResult =
  | { readonly ok: true; readonly state: WorkbenchState; readonly sessionId: string }
  | { readonly ok: false; readonly code: 'session_capacity_pinned'; readonly state: WorkbenchState }

export type WorkbenchPersistencePreparation =
  | {
      readonly ok: true
      readonly state: WorkbenchState
      readonly serialized: string
      readonly evictedSessionIds: readonly string[]
    }
  | {
      readonly ok: false
      readonly code: 'invalid_state' | 'protected_capacity_exceeded'
      readonly state: WorkbenchState
      readonly evictedSessionIds: readonly string[]
    }

export type EmployeeRoleMessagePreparation =
  | { readonly ok: true; readonly roleMessage: string }
  | {
      readonly ok: false
      readonly code: 'message_too_long'
      readonly maximumCharacters: number
      readonly actualCharacters: number
    }

export interface WorkbenchPersistenceText {
  readonly content: string
  readonly redacted: boolean
  readonly truncated: boolean
  readonly categories: readonly WorkbenchSensitiveCategory[]
}

export type WorkbenchSensitiveCategory =
  | 'provider_key'
  | 'bearer_or_jwt'
  | 'database_url'
  | 'private_key'
  | 'capability'
  | 'environment_secret'
  | 'url_credentials'
  | 'environment_locator'
  | 'physical_locator'
  | 'oversized_text'

export const PERSONAL_EMPLOYEES: readonly EmployeeDefinition[] = [
  {
    id: 'parent',
    displayName: '父 Agent',
    shortName: '父Agent',
    aliases: ['父Agent', '父agent', '负责人', 'OmniBase'],
    title: '项目负责人',
    responsibility: '理解目标、保持会话连续性、向唯一 Owner 汇报并组织当前任务。',
    boundary: '默认活动；不会自动唤醒其他员工，也不会开启后台自治。',
    defaultState: 'active',
  },
  {
    id: 'product',
    displayName: '产品经理',
    shortName: '产品',
    aliases: ['产品经理', '产品'],
    title: '产品经理',
    responsibility: '需求澄清、范围、优先级、验收标准与用户路径。',
    boundary: '不直接修改实现或代替安全、测试与发布职责。',
    defaultState: 'dormant',
  },
  {
    id: 'ux',
    displayName: 'UI/UX 设计师',
    shortName: 'UX',
    aliases: ['UI/UX', 'UIUX设计师', 'UI设计师', 'UX设计师', 'UX', 'UI'],
    title: 'UI/UX 设计师',
    responsibility: '信息架构、交互、视觉系统、可访问性与体验验收。',
    boundary: '不改后端授权或数据模型，不把视觉原型描述成已实现功能。',
    defaultState: 'dormant',
  },
  {
    id: 'frontend',
    displayName: '前端工程师',
    shortName: '前端',
    aliases: ['前端工程师', '前端'],
    title: '前端工程师',
    responsibility: 'Web 工作台、状态管理、客户端契约、交互性能与可访问性。',
    boundary: '不绕过服务端权限，不将浏览器状态当作安全授权。',
    defaultState: 'dormant',
  },
  {
    id: 'backend',
    displayName: '后端应用工程师',
    shortName: '后端',
    aliases: ['后端应用工程师', '后端工程师', '后端'],
    title: '后端应用工程师',
    responsibility: 'API、服务层、业务生命周期、幂等与事务边界。',
    boundary: '不擅自修改数据库迁移、安全策略或运维发布流程。',
    defaultState: 'dormant',
  },
  {
    id: 'data',
    displayName: '数据与存储工程师',
    shortName: '数据',
    aliases: ['数据与存储工程师', '数据工程师', '存储工程师', '数据'],
    title: '数据与存储工程师',
    responsibility: '数据模型、SQL、迁移、索引、备份、恢复与数据可视化边界。',
    boundary: '不访问未授权数据库，不对业务库执行破坏性试验。',
    defaultState: 'dormant',
  },
  {
    id: 'security',
    displayName: '安全架构师',
    shortName: '安全',
    aliases: ['安全架构师', '安全工程师', '安全'],
    title: '安全架构师',
    responsibility: '威胁模型、权限、秘密、审计、隔离与 fail-closed 设计。',
    boundary: '只评估和提出安全改动；不会自行批准生产外部效果。',
    defaultState: 'dormant',
  },
  {
    id: 'qa',
    displayName: '测试工程师',
    shortName: '测试',
    aliases: ['测试工程师', 'QA', '测试'],
    title: '测试工程师',
    responsibility: '验收路径、回归、攻击用例、可复现缺陷与证据质量。',
    boundary: '不以窄测试替代广泛声明，不把未执行项写成通过。',
    defaultState: 'dormant',
  },
  {
    id: 'operations',
    displayName: '运维与发布工程师',
    shortName: '运维',
    aliases: ['运维与发布工程师', '发布工程师', '运维工程师', '运维'],
    title: '运维与发布工程师',
    responsibility: '构建、CI、发布、监控、备份与恢复演练。',
    boundary: '没有 Owner 明确授权时不部署、不推送、不迁移业务数据库。',
    defaultState: 'dormant',
  },
  {
    id: 'docs',
    displayName: '技术文档工程师',
    shortName: '文档',
    aliases: ['技术文档工程师', '文档工程师', '文档'],
    title: '技术文档工程师',
    responsibility: '架构、交接、运行手册、用户说明与证据索引。',
    boundary: '文档必须忠于可执行行为，不得用文字替代实现证据。',
    defaultState: 'dormant',
  },
] as const

const EMPLOYEE_BY_ALIAS = new Map<string, EmployeeDefinition>()
function normalizeEmployeeAlias(value: string): string {
  return value.normalize('NFKC').toLocaleLowerCase()
}

for (const employee of PERSONAL_EMPLOYEES) {
  for (const alias of [employee.displayName, employee.shortName, ...employee.aliases]) {
    EMPLOYEE_BY_ALIAS.set(normalizeEmployeeAlias(alias), employee)
  }
}

const MENTION_PATTERN = /@([A-Za-z0-9_\-/\u4e00-\u9fff]+)/gu
const EMAIL_TOKEN_PATTERN = /[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+/gu
const BROADCAST_ALIASES = new Set(['all', 'everyone', '所有人', '全部', '全体', '全员'])
const SENSITIVE_PATTERNS: readonly {
  readonly category: Exclude<WorkbenchSensitiveCategory, 'oversized_text'>
  readonly pattern: RegExp
}[] = [
  {
    category: 'provider_key',
    pattern:
      /(?:\bsk-[A-Za-z0-9_-]{12,}\b|\b(?:ghp_|github_pat_|glpat-|xox[baprs]-|AKIA)[A-Za-z0-9_-]{12,}\b|\b(?:api[_-]?key|access[_-]?key|secret[_-]?key|github[_-]?token)\b\s*(?::|=|\s)\s*[^\s,;]{8,})/iu,
  },
  {
    category: 'bearer_or_jwt',
    pattern:
      /(?:\bBearer\s+[A-Za-z0-9._~+/=-]{8,}|\b(?:Authorization\s*:\s*)?Basic\s+[A-Za-z0-9+/=]{8,}|\beyJ[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,})/iu,
  },
  {
    category: 'database_url',
    pattern: /\b(?:postgres(?:ql)?|mysql|mariadb|mongodb(?:\+srv)?|redis):\/\/[^\s]+/iu,
  },
  {
    category: 'private_key',
    pattern: /-----BEGIN (?:[A-Z0-9 ]+ )?PRIVATE KEY-----/u,
  },
  {
    category: 'capability',
    pattern: /\b(?:capability|capability_token|grant_token)\b\s*[:=]\s*[^\s,;]{8,}/iu,
  },
  {
    category: 'environment_secret',
    pattern:
      /\b(?:[A-Z0-9_]*(?:API_KEY|ACCESS_TOKEN|REFRESH_TOKEN|TOKEN|SECRET|PASSWORD|PRIVATE_KEY|DATABASE_URL)|(?:OPENAI|ANTHROPIC|DEEPSEEK|KIMI|MOONSHOT|ZHIPU|GLM|POSTGRES|REDIS|JWT|SIGNING|SESSION|CAPABILITY|GITHUB)[A-Z0-9_]*)\b\s*(?::|=|\s)\s*[^\s,;]{8,}/iu,
  },
  {
    category: 'url_credentials',
    pattern: /\b[a-z][a-z0-9+.-]*:\/\/[^\s/:@]+:[^\s/@]+@/iu,
  },
  {
    category: 'environment_locator',
    pattern: /(?:^|[\\/])\.env(?:\.[A-Za-z0-9_-]+)?(?:$|[\s:])/iu,
  },
  {
    category: 'physical_locator',
    pattern:
      /(?:\b[A-Za-z]:\\[^\r\n]+|\\\\[^\\\s]+\\[^\\\s]+|(?:^|\s)\/(?:home|root|etc|var|run|mnt|Users)\/[^\r\n]*)/u,
  },
]

const EMPLOYEE_IDS = new Set<EmployeeId>(PERSONAL_EMPLOYEES.map((employee) => employee.id))
const MESSAGE_ROLES = new Set<WorkbenchMessageRole>(['user', 'agent', 'system'])
const TIMELINE_KINDS = new Set<WorkbenchTimelineKind>([
  'session_created',
  'session_renamed',
  'session_pinned',
  'session_unpinned',
  'session_archived',
  'session_restored',
  'employee_invoked',
  'invocation_started',
  'invocation_completed',
  'invocation_cancelled',
  'invocation_failed',
  'invocation_interrupted_unknown',
  'employee_returned_dormant',
  'message_added',
  'history_compacted',
])

function newId(prefix: string): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return `${prefix}-${crypto.randomUUID()}`
  }
  return `${prefix}-${Date.now()}-${Math.random().toString(16).slice(2)}`
}

function event(
  kind: WorkbenchTimelineKind,
  label: string,
  now: string,
  employeeId?: EmployeeId,
): WorkbenchTimelineEvent {
  const base = { id: newId('event'), kind, label, createdAt: now }
  return employeeId === undefined ? base : { ...base, employeeId }
}

function appendTimeline(
  timeline: readonly WorkbenchTimelineEvent[],
  ...events: readonly WorkbenchTimelineEvent[]
): readonly WorkbenchTimelineEvent[] {
  return [...timeline, ...events].slice(-P6_WORKBENCH_MAX_TIMELINE_EVENTS_PER_SESSION)
}

function jsonBytes(value: unknown): number {
  return new TextEncoder().encode(JSON.stringify(value)).byteLength
}

export function parseEmployeeInvocation(input: string): EmployeeInvocation {
  const trimmed = input.normalize('NFKC').trim()
  if (!trimmed) {
    return { ok: false, code: 'empty_message', message: '请输入任务内容。' }
  }

  // Email tokens are ordinary parent-Agent content. Every remaining at-sign is
  // routing syntax and therefore must be parsed completely and fail closed.
  const routingText = trimmed.replace(EMAIL_TOKEN_PATTERN, '')
  if (!routingText.includes('@')) {
    return {
      ok: true,
      employee: PERSONAL_EMPLOYEES[0]!,
      message: trimmed,
      explicitMention: false,
    }
  }

  const mentions = [...routingText.matchAll(MENTION_PATTERN)]
  const atCount = [...routingText].filter((character) => character === '@').length
  if (atCount !== mentions.length) {
    return {
      ok: false,
      code: 'invalid_mention',
      message: '检测到无法解析的 @；请使用一个完整的预制员工名称。',
    }
  }
  if (mentions.length > 1) {
    return {
      ok: false,
      code: 'multiple_employees',
      message: '一次只能 @ 一名专业员工；请拆成独立任务。',
    }
  }

  const rawAlias = mentions[0]?.[1] ?? ''
  if (BROADCAST_ALIASES.has(normalizeEmployeeAlias(rawAlias))) {
    return {
      ok: false,
      code: 'broadcast_employee',
      message: '不支持 @所有人 或广播；一次只能唤醒一名专业员工。',
    }
  }
  const employee = EMPLOYEE_BY_ALIAS.get(normalizeEmployeeAlias(rawAlias))
  if (!employee) {
    return {
      ok: false,
      code: 'unknown_employee',
      message: `没有名为 @${rawAlias} 的预制员工。`,
    }
  }

  const exactMention = mentions[0]?.[0] ?? ''
  const withoutMention = trimmed.replace(exactMention, '').trim()
  if (!withoutMention) {
    return { ok: false, code: 'empty_message', message: '请在 @员工 后描述具体任务。' }
  }

  return { ok: true, employee, message: withoutMention, explicitMention: true }
}

export function prepareEmployeeRoleMessage(
  employee: EmployeeDefinition,
  message: string,
): EmployeeRoleMessagePreparation {
  const roleMessage =
    employee.id === 'parent'
      ? message
      : `[P6.0 personal role context]\nRole: ${employee.title}\nResponsibility: ${employee.responsibility}\nBoundary: ${employee.boundary}\nDo not wake or delegate to another employee. Complete only this user's task and report to the Owner.\n\nUser task:\n${message}`
  if (roleMessage.length > P6_AGENT_ALPHA_MAX_MESSAGE_CHARACTERS) {
    return {
      ok: false,
      code: 'message_too_long',
      maximumCharacters: P6_AGENT_ALPHA_MAX_MESSAGE_CHARACTERS,
      actualCharacters: roleMessage.length,
    }
  }
  return { ok: true, roleMessage }
}

export function sanitizeWorkbenchPersistenceText(input: string): WorkbenchPersistenceText {
  const categories: WorkbenchSensitiveCategory[] = SENSITIVE_PATTERNS.filter(({ pattern }) =>
    pattern.test(input),
  ).map(({ category }) => category)
  const oversized = input.length > P6_WORKBENCH_MAX_MESSAGE_CHARACTERS
  if (oversized) categories.push('oversized_text')
  const uniqueCategories = [...new Set(categories)]
  const sensitiveCategories = uniqueCategories.filter((category) => category !== 'oversized_text')
  if (sensitiveCategories.length > 0) {
    return {
      content: PERSISTENCE_REDACTED_MARKER,
      redacted: true,
      truncated: false,
      categories: uniqueCategories,
    }
  }
  if (oversized) {
    return {
      content: `${input.slice(
        0,
        P6_WORKBENCH_MAX_MESSAGE_CHARACTERS - PERSISTENCE_TRUNCATED_MARKER.length,
      )}${PERSISTENCE_TRUNCATED_MARKER}`,
      redacted: false,
      truncated: true,
      categories: uniqueCategories,
    }
  }
  return {
    content: input,
    redacted: false,
    truncated: false,
    categories: [],
  }
}

function compactSessionToByteBudget(session: WorkbenchSession, now: string): WorkbenchSession {
  let messages = [...session.messages]
  let timeline = [...session.timeline]
  let removedMessages = 0
  let candidate: WorkbenchSession = { ...session, messages, timeline }

  while (jsonBytes(candidate) > P6_WORKBENCH_MAX_SESSION_BYTES && messages.length > 1) {
    messages = messages.slice(1)
    removedMessages += 1
    candidate = { ...candidate, messages }
  }
  if (removedMessages > 0) {
    timeline = [
      ...timeline,
      event(
        'history_compacted',
        `为保持本地会话字节上限，已移除 ${removedMessages} 条最旧消息`,
        now,
      ),
    ].slice(-P6_WORKBENCH_MAX_TIMELINE_EVENTS_PER_SESSION)
    candidate = { ...candidate, timeline }
  }
  while (jsonBytes(candidate) > P6_WORKBENCH_MAX_SESSION_BYTES && timeline.length > 1) {
    timeline = timeline.slice(1)
    candidate = { ...candidate, timeline }
  }
  while (jsonBytes(candidate) > P6_WORKBENCH_MAX_SESSION_BYTES && messages.length > 1) {
    messages = messages.slice(1)
    candidate = { ...candidate, messages }
  }
  return candidate
}

export function createWorkbenchSession(
  title = '新会话',
  now = new Date().toISOString(),
  workspaceId: string | null = null,
): WorkbenchSession {
  const id = newId('session')
  return {
    id,
    title,
    workspaceId,
    createdAt: now,
    updatedAt: now,
    pinned: false,
    archivedAt: null,
    messages: [],
    timeline: [event('session_created', '会话已创建', now)],
  }
}

export function createInitialWorkbenchState(now = new Date().toISOString()): WorkbenchState {
  const session = createWorkbenchSession('P6.0 工作台', now)
  return {
    schemaVersion: P6_WORKBENCH_SCHEMA_VERSION,
    activeSessionId: session.id,
    sessions: [session],
  }
}

export function addSession(
  state: WorkbenchState,
  title = '新会话',
  now = new Date().toISOString(),
  workspaceId: string | null = null,
): WorkbenchState {
  return tryAddSession(state, title, now, workspaceId).state
}

export function tryAddSession(
  state: WorkbenchState,
  title = '新会话',
  now = new Date().toISOString(),
  workspaceId: string | null = null,
): WorkbenchSessionAddResult {
  const removable = [...state.sessions]
    .filter((candidate) => !candidate.pinned && candidate.id !== state.activeSessionId)
    .sort((left, right) => left.updatedAt.localeCompare(right.updatedAt))
  if (state.sessions.length >= P6_WORKBENCH_MAX_SESSIONS && removable.length === 0) {
    return { ok: false, code: 'session_capacity_pinned', state }
  }
  const session = createWorkbenchSession(title, now, workspaceId)
  const removeCount = Math.max(0, state.sessions.length - P6_WORKBENCH_MAX_SESSIONS + 1)
  const removedIds = new Set(removable.slice(0, removeCount).map((candidate) => candidate.id))
  const retained = state.sessions.filter((candidate) => !removedIds.has(candidate.id))
  const next = { ...state, activeSessionId: session.id, sessions: [session, ...retained] }
  return { ok: true, state: next, sessionId: session.id }
}

export function setActiveSession(state: WorkbenchState, sessionId: string): WorkbenchState {
  return state.sessions.some((session) => session.id === sessionId)
    ? { ...state, activeSessionId: sessionId }
    : state
}

function updateSession(
  state: WorkbenchState,
  sessionId: string,
  updater: (session: WorkbenchSession) => WorkbenchSession,
): WorkbenchState {
  return {
    ...state,
    sessions: state.sessions.map((session) =>
      session.id === sessionId ? updater(session) : session,
    ),
  }
}

export function renameSession(
  state: WorkbenchState,
  sessionId: string,
  title: string,
  now = new Date().toISOString(),
): WorkbenchState {
  const sanitized = sanitizeWorkbenchPersistenceText(title.trim().slice(0, 80))
  const normalized = sanitized.content
  if (!normalized) return state
  return updateSession(state, sessionId, (session) => ({
    ...session,
    title: normalized,
    updatedAt: now,
    timeline: appendTimeline(
      session.timeline,
      event('session_renamed', `重命名为「${normalized}」`, now),
    ),
  }))
}

export function setSessionPinned(
  state: WorkbenchState,
  sessionId: string,
  pinned: boolean,
  now = new Date().toISOString(),
): WorkbenchState {
  return updateSession(state, sessionId, (session) => ({
    ...session,
    pinned,
    updatedAt: now,
    timeline: appendTimeline(
      session.timeline,
      event(
        pinned ? 'session_pinned' : 'session_unpinned',
        pinned ? '会话已固定' : '会话已取消固定',
        now,
      ),
    ),
  }))
}

export function setSessionArchived(
  state: WorkbenchState,
  sessionId: string,
  archived: boolean,
  now = new Date().toISOString(),
): WorkbenchState {
  const next = updateSession(state, sessionId, (session) => ({
    ...session,
    archivedAt: archived ? now : null,
    updatedAt: now,
    timeline: appendTimeline(
      session.timeline,
      event(
        archived ? 'session_archived' : 'session_restored',
        archived ? '会话已归档' : '会话已恢复',
        now,
      ),
    ),
  }))
  if (!archived || next.activeSessionId !== sessionId) return next
  const replacement = next.sessions.find((session) => session.archivedAt === null)
  if (replacement) return { ...next, activeSessionId: replacement.id }
  const added = tryAddSession(next, '新会话', now)
  // Archiving the last visible session must be atomic from the local product's
  // perspective. If every capacity slot is protected, keep the original
  // active session visible instead of leaving activeSessionId on an archive.
  return added.ok ? added.state : state
}

export function appendWorkbenchMessage(
  state: WorkbenchState,
  sessionId: string,
  message: Omit<WorkbenchMessage, 'id' | 'createdAt'>,
  now = new Date().toISOString(),
): WorkbenchState {
  return updateSession(state, sessionId, (session) => {
    const persisted = sanitizeWorkbenchPersistenceText(message.content)
    const nextMessage: WorkbenchMessage = {
      ...message,
      content: persisted.content,
      id: newId('message'),
      createdAt: now,
    }
    const employee = message.employeeId
      ? PERSONAL_EMPLOYEES.find((candidate) => candidate.id === message.employeeId)
      : null
    const invokedEvent =
      message.role === 'user' && employee
        ? [event('employee_invoked', `${employee.displayName} 已被本次消息唤醒`, now, employee.id)]
        : []
    return compactSessionToByteBudget(
      {
        ...session,
        updatedAt: now,
        messages: [...session.messages, nextMessage].slice(-P6_WORKBENCH_MAX_MESSAGES_PER_SESSION),
        timeline: appendTimeline(
          session.timeline,
          ...invokedEvent,
          event(
            'message_added',
            message.role === 'user' ? '用户消息已记录' : 'Agent 消息已记录',
            now,
            message.employeeId ?? undefined,
          ),
        ),
      },
      now,
    )
  })
}

export function appendWorkbenchTimelineEvent(
  state: WorkbenchState,
  sessionId: string,
  input: {
    kind: WorkbenchTimelineKind
    label: string
    employeeId?: EmployeeId
  },
  now = new Date().toISOString(),
): WorkbenchState {
  const label = sanitizeWorkbenchPersistenceText(
    input.label.slice(0, P6_WORKBENCH_MAX_TIMELINE_LABEL_CHARACTERS),
  ).content
  return updateSession(state, sessionId, (session) => ({
    ...session,
    updatedAt: now,
    timeline: appendTimeline(session.timeline, event(input.kind, label, now, input.employeeId)),
  }))
}

export function listWorkbenchSessions(
  state: WorkbenchState,
  options: { query?: string; archived?: boolean } = {},
): readonly WorkbenchSession[] {
  const query = options.query?.trim().toLocaleLowerCase() ?? ''
  const archived = options.archived ?? false
  return state.sessions
    .filter((session) => (archived ? session.archivedAt !== null : session.archivedAt === null))
    .filter((session) => {
      if (!query) return true
      return (
        session.title.toLocaleLowerCase().includes(query) ||
        session.messages.some((message) => message.content.toLocaleLowerCase().includes(query))
      )
    })
    .sort((left, right) => {
      if (left.pinned !== right.pinned) return left.pinned ? -1 : 1
      return right.updatedAt.localeCompare(left.updatedAt)
    })
}

export function estimateSessionTokens(session: WorkbenchSession): number {
  const characters = session.messages.reduce((total, message) => total + message.content.length, 0)
  return Math.ceil(characters / 3.2)
}

export function serializeWorkbenchState(state: WorkbenchState): string {
  return JSON.stringify(state)
}

export function prepareWorkbenchStateForPersistence(
  state: WorkbenchState,
): WorkbenchPersistencePreparation {
  const validated = deepValidateWorkbenchState(state)
  if (!validated) {
    return { ok: false, code: 'invalid_state', state, evictedSessionIds: [] }
  }
  let canonical: WorkbenchState = validated
  const activeSessionId = canonical.activeSessionId
  let sessions = [...canonical.sessions]
  const evictedSessionIds: string[] = []
  while (jsonBytes({ ...canonical, sessions }) > P6_WORKBENCH_MAX_STORE_BYTES) {
    const removable = sessions
      .filter((session) => !session.pinned && session.id !== activeSessionId)
      .sort((left, right) => left.updatedAt.localeCompare(right.updatedAt))[0]
    if (!removable) {
      return {
        ok: false,
        code: 'protected_capacity_exceeded',
        state: canonical,
        evictedSessionIds,
      }
    }
    sessions = sessions.filter((session) => session.id !== removable.id)
    evictedSessionIds.push(removable.id)
  }
  canonical = { ...canonical, sessions }
  const prepared = canonical
  return {
    ok: true,
    state: prepared,
    serialized: serializeWorkbenchState(prepared),
    evictedSessionIds,
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function hasExactKeys(
  value: Record<string, unknown>,
  required: readonly string[],
  optional: readonly string[] = [],
): boolean {
  const allowed = new Set([...required, ...optional])
  return (
    required.every((key) => Object.hasOwn(value, key)) &&
    Object.keys(value).every((key) => allowed.has(key))
  )
}

function boundedString(value: unknown, maximum: number, allowEmpty = false): value is string {
  return (
    typeof value === 'string' && value.length <= maximum && (allowEmpty || value.trim().length > 0)
  )
}

function isoInstant(value: unknown): value is string {
  return (
    boundedString(value, P6_WORKBENCH_MAX_DATE_CHARACTERS) &&
    Number.isFinite(Date.parse(value)) &&
    new Date(value).toISOString() === value
  )
}

function parseMessage(value: unknown, ids: Set<string>): WorkbenchMessage | null {
  if (!isRecord(value)) return null
  if (!hasExactKeys(value, ['id', 'role', 'employeeId', 'content', 'createdAt'])) return null
  if (!boundedString(value.id, P6_WORKBENCH_MAX_ID_CHARACTERS) || ids.has(value.id)) return null
  if (typeof value.role !== 'string' || !MESSAGE_ROLES.has(value.role as WorkbenchMessageRole))
    return null
  if (
    value.employeeId !== null &&
    (typeof value.employeeId !== 'string' || !EMPLOYEE_IDS.has(value.employeeId as EmployeeId))
  )
    return null
  if (!boundedString(value.content, P6_WORKBENCH_MAX_MESSAGE_CHARACTERS, true)) return null
  if (!isoInstant(value.createdAt)) return null
  ids.add(value.id)
  return {
    id: value.id,
    role: value.role as WorkbenchMessageRole,
    employeeId: value.employeeId as EmployeeId | null,
    content: sanitizeWorkbenchPersistenceText(value.content).content,
    createdAt: value.createdAt,
  }
}

function parseTimelineEvent(value: unknown, ids: Set<string>): WorkbenchTimelineEvent | null {
  if (!isRecord(value)) return null
  if (!hasExactKeys(value, ['id', 'kind', 'label', 'createdAt'], ['employeeId'])) return null
  if (!boundedString(value.id, P6_WORKBENCH_MAX_ID_CHARACTERS) || ids.has(value.id)) return null
  if (typeof value.kind !== 'string' || !TIMELINE_KINDS.has(value.kind as WorkbenchTimelineKind))
    return null
  if (!boundedString(value.label, P6_WORKBENCH_MAX_TIMELINE_LABEL_CHARACTERS, true)) return null
  if (!isoInstant(value.createdAt)) return null
  if (
    value.employeeId !== undefined &&
    (typeof value.employeeId !== 'string' || !EMPLOYEE_IDS.has(value.employeeId as EmployeeId))
  )
    return null
  ids.add(value.id)
  const parsed: WorkbenchTimelineEvent = {
    id: value.id,
    kind: value.kind as WorkbenchTimelineKind,
    label: sanitizeWorkbenchPersistenceText(value.label).content,
    createdAt: value.createdAt,
    ...(value.employeeId === undefined ? {} : { employeeId: value.employeeId as EmployeeId }),
  }
  return parsed
}

function parseSession(
  value: unknown,
  sessionIds: Set<string>,
  messageIds: Set<string>,
  timelineIds: Set<string>,
): WorkbenchSession | null {
  if (!isRecord(value)) return null
  if (
    !hasExactKeys(value, [
      'id',
      'title',
      'workspaceId',
      'createdAt',
      'updatedAt',
      'pinned',
      'archivedAt',
      'messages',
      'timeline',
    ])
  )
    return null
  if (!boundedString(value.id, P6_WORKBENCH_MAX_ID_CHARACTERS) || sessionIds.has(value.id))
    return null
  if (!boundedString(value.title, 80)) return null
  if (
    value.workspaceId !== null &&
    !boundedString(value.workspaceId, P6_WORKBENCH_MAX_WORKSPACE_ID_CHARACTERS)
  )
    return null
  if (!isoInstant(value.createdAt) || !isoInstant(value.updatedAt)) return null
  if (value.updatedAt < value.createdAt) return null
  if (typeof value.pinned !== 'boolean') return null
  if (value.archivedAt !== null && !isoInstant(value.archivedAt)) return null
  if (
    !Array.isArray(value.messages) ||
    value.messages.length > P6_WORKBENCH_MAX_MESSAGES_PER_SESSION
  )
    return null
  if (
    !Array.isArray(value.timeline) ||
    value.timeline.length > P6_WORKBENCH_MAX_TIMELINE_EVENTS_PER_SESSION
  )
    return null
  const messages: WorkbenchMessage[] = []
  for (const item of value.messages) {
    const parsed = parseMessage(item, messageIds)
    if (!parsed) return null
    messages.push(parsed)
  }
  const timeline: WorkbenchTimelineEvent[] = []
  for (const item of value.timeline) {
    const parsed = parseTimelineEvent(item, timelineIds)
    if (!parsed) return null
    timeline.push(parsed)
  }
  sessionIds.add(value.id)
  const session: WorkbenchSession = {
    id: value.id,
    title: sanitizeWorkbenchPersistenceText(value.title).content,
    workspaceId: value.workspaceId,
    createdAt: value.createdAt,
    updatedAt: value.updatedAt,
    pinned: value.pinned,
    archivedAt: value.archivedAt,
    messages,
    timeline,
  }
  return jsonBytes(session) <= P6_WORKBENCH_MAX_SESSION_BYTES ? session : null
}

function deepValidateWorkbenchState(value: unknown): WorkbenchState | null {
  if (!isRecord(value) || !hasExactKeys(value, ['schemaVersion', 'activeSessionId', 'sessions']))
    return null
  if (value.schemaVersion !== P6_WORKBENCH_SCHEMA_VERSION) return null
  if (!boundedString(value.activeSessionId, P6_WORKBENCH_MAX_ID_CHARACTERS)) return null
  if (
    !Array.isArray(value.sessions) ||
    value.sessions.length === 0 ||
    value.sessions.length > P6_WORKBENCH_MAX_SESSIONS
  )
    return null
  const sessionIds = new Set<string>()
  const messageIds = new Set<string>()
  const timelineIds = new Set<string>()
  const sessions: WorkbenchSession[] = []
  for (const item of value.sessions) {
    const parsed = parseSession(item, sessionIds, messageIds, timelineIds)
    if (!parsed) return null
    sessions.push(parsed)
  }
  if (!sessionIds.has(value.activeSessionId)) return null
  return {
    schemaVersion: P6_WORKBENCH_SCHEMA_VERSION,
    activeSessionId: value.activeSessionId,
    sessions,
  }
}

export function parseWorkbenchState(raw: string | null): WorkbenchState | null {
  if (!raw || new TextEncoder().encode(raw).byteLength > P6_WORKBENCH_MAX_STORE_BYTES) return null
  try {
    const value: unknown = JSON.parse(raw)
    const restored = deepValidateWorkbenchState(value)
    if (!restored) return null
    const recoveredAt = new Date().toISOString()
    return {
      ...restored,
      sessions: restored.sessions.map((session) => {
        const lastStarted = session.timeline.findLastIndex(
          (item) => item.kind === 'invocation_started',
        )
        if (lastStarted < 0) return session
        const hasTerminal = session.timeline
          .slice(lastStarted + 1)
          .some((item) =>
            [
              'invocation_completed',
              'invocation_cancelled',
              'invocation_failed',
              'invocation_interrupted_unknown',
            ].includes(item.kind),
          )
        if (hasTerminal) return session
        const employeeId = session.timeline[lastStarted]?.employeeId
        return {
          ...session,
          updatedAt: recoveredAt,
          timeline: appendTimeline(
            session.timeline,
            event(
              'invocation_interrupted_unknown',
              '页面恢复时发现未完成调用；结果标记为 unknown，未自动重放 Provider 请求',
              recoveredAt,
              employeeId,
            ),
            ...(employeeId && employeeId !== 'parent'
              ? [
                  event(
                    'employee_returned_dormant',
                    `${employeeByStableId(employeeId).displayName} 已恢复静默`,
                    recoveredAt,
                    employeeId,
                  ),
                ]
              : []),
          ),
        }
      }),
    }
  } catch {
    return null
  }
}

function employeeByStableId(id: EmployeeId): EmployeeDefinition {
  return PERSONAL_EMPLOYEES.find((employee) => employee.id === id) ?? PERSONAL_EMPLOYEES[0]!
}
