import assert from 'node:assert/strict'
import test from 'node:test'
import {
  P6_AGENT_ALPHA_MAX_MESSAGE_CHARACTERS,
  P6_WORKBENCH_MAX_MESSAGE_CHARACTERS,
  P6_WORKBENCH_MAX_SESSION_BYTES,
  P6_WORKBENCH_MAX_SESSIONS,
  P6_WORKBENCH_MAX_STORE_BYTES,
  PERSONAL_EMPLOYEES,
  addSession,
  appendWorkbenchMessage,
  appendWorkbenchTimelineEvent,
  createInitialWorkbenchState,
  estimateSessionTokens,
  listWorkbenchSessions,
  parseEmployeeInvocation,
  parseWorkbenchState,
  prepareEmployeeRoleMessage,
  prepareWorkbenchStateForPersistence,
  renameSession,
  sanitizeWorkbenchPersistenceText,
  serializeWorkbenchState,
  setSessionArchived,
  setSessionPinned,
  tryAddSession,
} from './p6-workbench'

test('personal roster has one active parent and nine dormant specialists', () => {
  assert.equal(PERSONAL_EMPLOYEES.length, 10)
  assert.deepEqual(
    PERSONAL_EMPLOYEES.filter((employee) => employee.defaultState === 'active').map(
      (employee) => employee.id,
    ),
    ['parent'],
  )
  assert.equal(
    PERSONAL_EMPLOYEES.filter((employee) => employee.defaultState === 'dormant').length,
    9,
  )
})

test('every employee quick-mention alias round-trips through mention routing', () => {
  for (const employee of PERSONAL_EMPLOYEES) {
    const result = parseEmployeeInvocation(`@${employee.shortName} 执行职责内任务`)
    assert.equal(result.ok, true, employee.shortName)
    if (!result.ok) continue
    assert.equal(result.employee.id, employee.id, employee.shortName)
  }
  const uxDisplayPrefix = parseEmployeeInvocation('@UI/UX 执行职责内任务')
  assert.equal(uxDisplayPrefix.ok, true)
  if (uxDisplayPrefix.ok) assert.equal(uxDisplayPrefix.employee.id, 'ux')
})

test('messages without an @ target stay with the parent Agent', () => {
  const result = parseEmployeeInvocation('继续整理当前方案')
  assert.equal(result.ok, true)
  if (!result.ok) return
  assert.equal(result.employee.id, 'parent')
  assert.equal(result.explicitMention, false)
})

test('one exact specialist mention wakes only that employee', () => {
  const result = parseEmployeeInvocation('@前端工程师 请审查工作台布局')
  assert.equal(result.ok, true)
  if (!result.ok) return
  assert.equal(result.employee.id, 'frontend')
  assert.equal(result.message, '请审查工作台布局')
  assert.equal(result.explicitMention, true)
})

test('full-width at sign is normalized before employee routing', () => {
  const result = parseEmployeeInvocation('＠安全架构师 检查权限边界')
  assert.equal(result.ok, true)
  if (!result.ok) return
  assert.equal(result.employee.id, 'security')
  assert.equal(result.message, '检查权限边界')
})

test('multiple, unknown and empty employee invocations fail closed', () => {
  assert.deepEqual(parseEmployeeInvocation('@前端 @安全 检查'), {
    ok: false,
    code: 'multiple_employees',
    message: '一次只能 @ 一名专业员工；请拆成独立任务。',
  })
  assert.deepEqual(parseEmployeeInvocation('@市场经理 看一下'), {
    ok: false,
    code: 'unknown_employee',
    message: '没有名为 @市场经理 的预制员工。',
  })
  assert.deepEqual(parseEmployeeInvocation('@测试工程师'), {
    ok: false,
    code: 'empty_message',
    message: '请在 @员工 后描述具体任务。',
  })
})

test('bare, malformed and broadcast mentions fail closed while email stays parent content', () => {
  for (const value of ['@', '@ 安全 检查', '检查 @', '@所有人 检查', '@all 检查']) {
    assert.equal(parseEmployeeInvocation(value).ok, false, value)
  }
  const email = parseEmployeeInvocation('请联系 owner@example.com 确认')
  assert.equal(email.ok, true)
  if (!email.ok) return
  assert.equal(email.employee.id, 'parent')
  assert.equal(email.explicitMention, false)
  const emailAndEmployee = parseEmployeeInvocation('联系 owner@example.com，@安全架构师 检查')
  assert.equal(emailAndEmployee.ok, true)
  if (!emailAndEmployee.ok) return
  assert.equal(emailAndEmployee.employee.id, 'security')
  assert.equal(emailAndEmployee.message, '联系 owner@example.com, 检查')
})

test('specialist role wrapper is included in the exact 32000 character preflight', () => {
  const parent = PERSONAL_EMPLOYEES[0]!
  assert.equal(
    prepareEmployeeRoleMessage(parent, 'x'.repeat(P6_AGENT_ALPHA_MAX_MESSAGE_CHARACTERS)).ok,
    true,
  )
  assert.equal(
    prepareEmployeeRoleMessage(parent, 'x'.repeat(P6_AGENT_ALPHA_MAX_MESSAGE_CHARACTERS + 1)).ok,
    false,
  )
  const frontend = PERSONAL_EMPLOYEES.find((employee) => employee.id === 'frontend')!
  assert.equal(
    prepareEmployeeRoleMessage(frontend, 'x'.repeat(P6_AGENT_ALPHA_MAX_MESSAGE_CHARACTERS)).ok,
    false,
  )
})

test('sessions create, rename, pin, search, archive and restore deterministically', () => {
  const t0 = '2026-08-13T00:00:00.000Z'
  const t1 = '2026-08-13T00:01:00.000Z'
  const t2 = '2026-08-13T00:02:00.000Z'
  let state = createInitialWorkbenchState(t0)
  const originalId = state.activeSessionId
  state = renameSession(state, originalId, '前端工作台', t1)
  state = setSessionPinned(state, originalId, true, t2)
  state = appendWorkbenchMessage(
    state,
    originalId,
    { role: 'user', employeeId: 'frontend', content: '实现会话侧栏' },
    t2,
  )
  state = addSession(state, '第二个会话', t2)

  assert.equal(listWorkbenchSessions(state)[0]?.id, originalId)
  assert.equal(listWorkbenchSessions(state, { query: '会话侧栏' }).length, 1)
  assert.equal(estimateSessionTokens(state.sessions.find((item) => item.id === originalId)!), 2)

  state = setSessionArchived(state, originalId, true, t2)
  assert.equal(
    listWorkbenchSessions(state).some((item) => item.id === originalId),
    false,
  )
  assert.equal(listWorkbenchSessions(state, { archived: true })[0]?.id, originalId)
  state = setSessionArchived(state, originalId, false, t2)
  assert.equal(
    listWorkbenchSessions(state).some((item) => item.id === originalId),
    true,
  )
})

test('new sessions can bind the selected Workspace without crossing existing sessions', () => {
  const state = createInitialWorkbenchState('2026-08-13T00:00:00.000Z')
  const originalId = state.activeSessionId
  const next = addSession(state, 'Workspace B', '2026-08-13T00:01:00.000Z', 'workspace-b')
  assert.equal(
    next.sessions.find((session) => session.id === next.activeSessionId)?.workspaceId,
    'workspace-b',
  )
  assert.equal(next.sessions.find((session) => session.id === originalId)?.workspaceId, null)
})

test('capacity never silently evicts pinned or active sessions', () => {
  let state = createInitialWorkbenchState('2026-08-13T00:00:00.000Z')
  state = setSessionPinned(state, state.activeSessionId, true)
  for (let index = 1; index < P6_WORKBENCH_MAX_SESSIONS; index += 1) {
    state = addSession(
      state,
      `session-${index}`,
      `2026-08-13T00:${String(index).padStart(2, '0')}:00.000Z`,
    )
    state = setSessionPinned(state, state.activeSessionId, true)
  }
  const ids = state.sessions.map((session) => session.id)
  const result = tryAddSession(state, 'overflow')
  assert.equal(result.ok, false)
  assert.deepEqual(
    result.state.sessions.map((session) => session.id),
    ids,
  )
})

test('archiving the last visible session rolls back when protected capacity blocks replacement', () => {
  let state = createInitialWorkbenchState('2026-08-13T00:00:00.000Z')
  state = setSessionPinned(state, state.activeSessionId, true)
  for (let index = 1; index < P6_WORKBENCH_MAX_SESSIONS; index += 1) {
    const previousActiveId = state.activeSessionId
    state = addSession(
      state,
      `session-${index}`,
      `2026-08-13T00:${String(index).padStart(2, '0')}:00.000Z`,
    )
    state = setSessionPinned(state, state.activeSessionId, true)
    state = setSessionArchived(state, previousActiveId, true)
  }
  const activeId = state.activeSessionId
  const archived = setSessionArchived(state, activeId, true, '2026-08-13T02:00:00.000Z')
  assert.equal(archived, state)
  assert.equal(archived.activeSessionId, activeId)
  assert.equal(archived.sessions.find((session) => session.id === activeId)?.archivedAt, null)
})

test('stored state round-trips and malformed or future schemas fail closed', () => {
  const state = createInitialWorkbenchState('2026-08-13T00:00:00.000Z')
  assert.deepEqual(parseWorkbenchState(serializeWorkbenchState(state)), state)
  assert.equal(parseWorkbenchState('{broken'), null)
  assert.equal(parseWorkbenchState(JSON.stringify({ ...state, schemaVersion: 2 })), null)
  assert.equal(parseWorkbenchState(JSON.stringify({ ...state, activeSessionId: 'missing' })), null)
})

test('stored state rejects unknown fields, duplicate ids, invalid enums and nested nulls', () => {
  const state = createInitialWorkbenchState('2026-08-13T00:00:00.000Z')
  const session = state.sessions[0]!
  const message = {
    id: 'message-one',
    role: 'user',
    employeeId: 'parent',
    content: 'hello',
    createdAt: '2026-08-13T00:00:00.000Z',
  }
  const withMessage = { ...state, sessions: [{ ...session, messages: [message] }] }
  const cases = [
    { ...state, unexpected: true },
    { ...state, sessions: [{ ...session, unexpected: true }] },
    { ...state, sessions: [{ ...session, messages: [null] }] },
    { ...state, sessions: [{ ...session, messages: [{ ...message, role: 'tool' }] }] },
    { ...state, sessions: [{ ...session, messages: [{ ...message, employeeId: 'unknown' }] }] },
    {
      ...withMessage,
      sessions: [{ ...withMessage.sessions[0], messages: [message, message] }],
    },
    {
      ...state,
      sessions: [{ ...session, timeline: [{ ...session.timeline[0], kind: 'unknown' }] }],
    },
  ]
  for (const value of cases) assert.equal(parseWorkbenchState(JSON.stringify(value)), null)
})

test('stored state rejects count, string and byte budget violations', () => {
  const state = createInitialWorkbenchState('2026-08-13T00:00:00.000Z')
  const session = state.sessions[0]!
  assert.equal(
    parseWorkbenchState(
      JSON.stringify({
        ...state,
        sessions: Array.from({ length: P6_WORKBENCH_MAX_SESSIONS + 1 }, (_, index) => ({
          ...session,
          id: `session-${index}`,
        })),
      }),
    ),
    null,
  )
  assert.equal(
    parseWorkbenchState(
      JSON.stringify({ ...state, sessions: [{ ...session, title: 'x'.repeat(81) }] }),
    ),
    null,
  )
  assert.equal(parseWorkbenchState(' '.repeat(P6_WORKBENCH_MAX_STORE_BYTES + 1)), null)
})

test('sensitive user and Agent text is replaced by a category-only marker', () => {
  const samples = [
    ['sk-1234567890abcdef', 'provider_key'],
    ['Authorization: Bearer abcdefghijklmnop', 'bearer_or_jwt'],
    ['postgresql://owner:password@database/db', 'database_url'],
    ['-----BEGIN PRIVATE KEY-----', 'private_key'],
    ['capability_token=opaque-secret-token', 'capability'],
    ['OPENAI_API_KEY=secret-value', 'environment_secret'],
    ['Authorization: Basic dXNlcjpwYXNzd29yZA==', 'bearer_or_jwt'],
    ['GITHUB_TOKEN ghp_1234567890abcdefghijklmnop', 'provider_key'],
    ['https://owner:opaque-password@example.com/path', 'url_credentials'],
    ['C:\\Users\\Owner\\project\\.env', 'environment_locator'],
    ['/home/owner/project/file.txt', 'physical_locator'],
  ] as const
  for (const [value, category] of samples) {
    const result = sanitizeWorkbenchPersistenceText(value)
    assert.equal(result.redacted, true, value)
    assert.equal(result.categories.includes(category), true, value)
    assert.equal(result.content.includes(value), false, value)
    assert.equal(result.content, '[OMNIBASE_LOCAL_REDACTED]')
  }
  assert.deepEqual(sanitizeWorkbenchPersistenceText('普通产品讨论'), {
    content: '普通产品讨论',
    redacted: false,
    truncated: false,
    categories: [],
  })
})

test('oversized non-sensitive Agent text is truncated with an explicit marker', () => {
  const original = '长'.repeat(P6_WORKBENCH_MAX_MESSAGE_CHARACTERS + 100)
  const result = sanitizeWorkbenchPersistenceText(original)
  assert.equal(result.redacted, false)
  assert.equal(result.truncated, true)
  assert.deepEqual(result.categories, ['oversized_text'])
  assert.equal(result.content.length, P6_WORKBENCH_MAX_MESSAGE_CHARACTERS)
  assert.match(result.content, /\[OMNIBASE_LOCAL_TRUNCATED\]$/)
})

test('oversized sensitive text remains fully redacted instead of exposing a prefix', () => {
  const original = `sk-1234567890abcdef${'x'.repeat(P6_WORKBENCH_MAX_MESSAGE_CHARACTERS)}`
  const result = sanitizeWorkbenchPersistenceText(original)
  assert.equal(result.redacted, true)
  assert.equal(result.truncated, false)
  assert.equal(result.content, '[OMNIBASE_LOCAL_REDACTED]')
  assert.equal(result.categories.includes('provider_key'), true)
  assert.equal(result.categories.includes('oversized_text'), true)
})

test('append sanitizes secrets before they enter the durable projection', () => {
  let state = createInitialWorkbenchState('2026-08-13T00:00:00.000Z')
  state = appendWorkbenchMessage(state, state.activeSessionId, {
    role: 'agent',
    employeeId: 'parent',
    content: 'Bearer abcdefghijklmnop',
  })
  const content = state.sessions[0]?.messages[0]?.content ?? ''
  assert.match(content, /^\[OMNIBASE_LOCAL_REDACTED\]/)
  assert.equal(content.includes('abcdefghijklmnop'), false)
})

test('persistence preparation validates state and preserves protected sessions', () => {
  const state = createInitialWorkbenchState('2026-08-13T00:00:00.000Z')
  const prepared = prepareWorkbenchStateForPersistence(state)
  assert.equal(prepared.ok, true)
  if (!prepared.ok) return
  assert.deepEqual(parseWorkbenchState(prepared.serialized), prepared.state)
  assert.deepEqual(prepared.evictedSessionIds, [])
  const invalid = prepareWorkbenchStateForPersistence({
    ...state,
    sessions: [{ ...state.sessions[0]!, messages: [null] as never }],
  })
  assert.equal(invalid.ok, false)
})

test('legal message growth compacts one session before it can poison store persistence', () => {
  let state = createInitialWorkbenchState('2026-08-13T00:00:00.000Z')
  const sessionId = state.activeSessionId
  for (let index = 0; index < 80; index += 1) {
    state = appendWorkbenchMessage(
      state,
      sessionId,
      { role: 'agent', employeeId: 'parent', content: `${index}:${'长'.repeat(12_000)}` },
      `2026-08-13T00:${String(index % 60).padStart(2, '0')}:00.000Z`,
    )
  }
  const session = state.sessions[0]!
  assert.ok(
    new TextEncoder().encode(JSON.stringify(session)).byteLength <= P6_WORKBENCH_MAX_SESSION_BYTES,
  )
  assert.equal(session.messages.at(-1)?.content.startsWith('79:'), true)
  assert.equal(session.messages.length < 80, true)
  assert.equal(
    session.timeline.some((item) => item.kind === 'history_compacted'),
    true,
  )
  const prepared = prepareWorkbenchStateForPersistence(state)
  assert.equal(prepared.ok, true)
  if (!prepared.ok) return
  assert.notEqual(parseWorkbenchState(prepared.serialized), null)
})

test('an unterminated invocation restores as unknown without provider replay', () => {
  let state = createInitialWorkbenchState('2026-08-13T00:00:00.000Z')
  const sessionId = state.activeSessionId
  state = appendWorkbenchTimelineEvent(
    state,
    sessionId,
    {
      kind: 'invocation_started',
      label: '前端工程师 调用已开始',
      employeeId: 'frontend',
    },
    '2026-08-13T00:01:00.000Z',
  )
  const restored = parseWorkbenchState(serializeWorkbenchState(state))
  assert.notEqual(restored, null)
  const timeline = restored?.sessions[0]?.timeline ?? []
  assert.equal(timeline.at(-2)?.kind, 'invocation_interrupted_unknown')
  assert.equal(timeline.at(-1)?.kind, 'employee_returned_dormant')
  assert.match(timeline.at(-2)?.label ?? '', /未自动重放 Provider 请求/)
})

test('a terminal invocation is not rewritten as interrupted on restore', () => {
  const startedAt = '2026-08-13T00:00:00.000Z'
  const completedAt = '2026-08-13T00:01:00.000Z'
  let state = createInitialWorkbenchState(startedAt)
  const sessionId = state.activeSessionId
  state = appendWorkbenchTimelineEvent(
    state,
    sessionId,
    { kind: 'invocation_started', label: '调用开始', employeeId: 'parent' },
    startedAt,
  )
  state = appendWorkbenchTimelineEvent(
    state,
    sessionId,
    { kind: 'invocation_completed', label: '调用完成', employeeId: 'parent' },
    completedAt,
  )
  const restored = parseWorkbenchState(serializeWorkbenchState(state))
  assert.equal(
    restored?.sessions[0]?.timeline.some((item) => item.kind === 'invocation_interrupted_unknown'),
    false,
  )
})
