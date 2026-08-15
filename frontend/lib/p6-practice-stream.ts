import type { P6EmployeeRoleId } from './types'

export type P6PracticeScenario = 'rag' | 'artifact' | 'workspace'

export interface P6PracticeUsage {
  input_tokens: number
  output_tokens: number
  total_tokens: number
  reasoning_tokens: number
  cached_input_tokens: number
  cache_miss_input_tokens: number
}

export interface P6PracticeCitation {
  index: number
  chunkId: string
  documentId: string
  pageNumber: number
}

export interface P6PracticeNodeReceipt {
  ordinal: number
  role: P6EmployeeRoleId
  invocationId: string
  taskId: string
  requestedModelId: string
  actualModelId: string
  usage: P6PracticeUsage
  answerSha256: string
  citations: readonly P6PracticeCitation[]
}

export interface P6PracticeStarted {
  scenario: P6PracticeScenario
  participantCount: number
  roles: readonly P6EmployeeRoleId[]
  serial: true
  enterpriseMultiAgent: false
}

export type P6PracticeTerminal =
  | {
      kind: 'done'
      scenario: P6PracticeScenario
      participantCount: number
      providerCallCount: number
      parentInvocationId: string
      parentTaskId: string
      finalAnswer: string
      finalAnswerSha256: string
      nodes: readonly P6PracticeNodeReceipt[]
    }
  | { kind: 'cancelled' }
  | { kind: 'error'; code: string }

export interface P6PracticeStreamCallbacks {
  onStarted?: (started: P6PracticeStarted) => void
  onNodeStarted?: (value: { ordinal: number; role: P6EmployeeRoleId }) => void
  onNodeIdentity?: (value: {
    ordinal: number
    role: P6EmployeeRoleId
    invocationId: string
    taskId: string
    requestedModelId: string
  }) => void
  onNodeCompleted?: (receipt: P6PracticeNodeReceipt) => void
}

interface ActiveNode {
  readonly ordinal: number
  readonly role: P6EmployeeRoleId
  identity: {
    readonly invocationId: string
    readonly taskId: string
    readonly requestedModelId: string
  } | null
  usageObserved: boolean
  citations: readonly P6PracticeCitation[] | null
}

interface ParsedFrame {
  readonly event: string
  readonly payload: Record<string, unknown>
}

const MAX_STREAM_CHARACTERS = 1024 * 1024
const SHA256 = /^[0-9a-f]{64}$/
const COUNTS = new Set([1, 3, 4, 5, 6])
const ROLES = new Set<P6EmployeeRoleId>([
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
])

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null
}

function parseFrame(raw: string): ParsedFrame | null {
  let event = ''
  const data: string[] = []
  for (const line of raw.split(/\r?\n/)) {
    if (line.startsWith('event:')) event = line.slice(6).trim()
    if (line.startsWith('data:')) {
      const value = line.slice(5)
      data.push(value.startsWith(' ') ? value.slice(1) : value)
    }
  }
  if (!event || data.length === 0) return null
  let payload: unknown
  try {
    payload = JSON.parse(data.join('\n'))
  } catch {
    throw new Error('p6_practice_stream_malformed')
  }
  if (!isRecord(payload)) throw new Error('p6_practice_stream_malformed')
  return { event, payload }
}

function takeFrames(buffer: string, flush: boolean): readonly [ParsedFrame[], string] {
  const frames: ParsedFrame[] = []
  let remaining = buffer
  const boundary = /\r?\n\r?\n/
  while (true) {
    const match = boundary.exec(remaining)
    if (!match || match.index === undefined) break
    const parsed = parseFrame(remaining.slice(0, match.index))
    remaining = remaining.slice(match.index + match[0].length)
    if (parsed) frames.push(parsed)
  }
  if (flush && remaining.trim()) {
    const parsed = parseFrame(remaining)
    if (parsed) frames.push(parsed)
    remaining = ''
  }
  return [frames, remaining]
}

function practiceRole(value: unknown): P6EmployeeRoleId | null {
  return typeof value === 'string' && ROLES.has(value as P6EmployeeRoleId)
    ? (value as P6EmployeeRoleId)
    : null
}

function finiteInteger(value: unknown): number | null {
  return typeof value === 'number' && Number.isInteger(value) && value >= 0 ? value : null
}

function requiredString(value: unknown): string | null {
  return typeof value === 'string' && value.length > 0 ? value : null
}

function parseUsage(value: unknown): P6PracticeUsage | null {
  if (!isRecord(value)) return null
  const usage = {
    input_tokens: finiteInteger(value.input_tokens),
    output_tokens: finiteInteger(value.output_tokens),
    total_tokens: finiteInteger(value.total_tokens),
    reasoning_tokens: finiteInteger(value.reasoning_tokens),
    cached_input_tokens: finiteInteger(value.cached_input_tokens),
    cache_miss_input_tokens: finiteInteger(value.cache_miss_input_tokens),
  }
  if (Object.values(usage).some((item) => item === null)) return null
  const parsed = usage as P6PracticeUsage
  if (parsed.total_tokens < parsed.input_tokens + parsed.output_tokens) return null
  if (parsed.cached_input_tokens + parsed.cache_miss_input_tokens > parsed.input_tokens) return null
  return parsed
}

function parseCitations(value: unknown): P6PracticeCitation[] | null {
  if (!Array.isArray(value) || value.length > 8) return null
  const citations: P6PracticeCitation[] = []
  for (const [offset, item] of value.entries()) {
    if (!isRecord(item)) return null
    const index = finiteInteger(item.index)
    const chunkId = requiredString(item.chunk_id)
    const documentId = requiredString(item.document_id)
    const pageNumber = finiteInteger(item.page_number)
    if (
      index !== offset + 1 ||
      chunkId === null ||
      chunkId.length > 128 ||
      documentId === null ||
      documentId.length > 128 ||
      pageNumber === null ||
      pageNumber < 1
    ) {
      return null
    }
    citations.push({ index, chunkId, documentId, pageNumber })
  }
  return citations
}

function parseStarted(payload: Record<string, unknown>): P6PracticeStarted | null {
  const scenario = payload.scenario
  const count = finiteInteger(payload.participant_count)
  const roles = Array.isArray(payload.roles) ? payload.roles.map(practiceRole) : []
  if (
    (scenario !== 'rag' && scenario !== 'artifact' && scenario !== 'workspace') ||
    count === null ||
    !COUNTS.has(count) ||
    roles.length !== count ||
    roles.some((role) => role === null) ||
    new Set(roles).size !== roles.length ||
    roles.at(-1) !== 'parent' ||
    payload.serial !== true ||
    payload.enterprise_multi_agent !== false
  ) {
    return null
  }
  return {
    scenario,
    participantCount: count,
    roles: roles as P6EmployeeRoleId[],
    serial: true,
    enterpriseMultiAgent: false,
  }
}

function parseNodeReceipt(payload: Record<string, unknown>): P6PracticeNodeReceipt | null {
  const ordinal = finiteInteger(payload.ordinal)
  const role = practiceRole(payload.role)
  const invocationId = requiredString(payload.invocation_id)
  const taskId = requiredString(payload.task_id)
  const requestedModelId = requiredString(payload.requested_model_id)
  const actualModelId = requiredString(payload.actual_model_id)
  const usage = parseUsage(payload.usage)
  const answerSha256 = requiredString(payload.answer_sha256)
  const citations = parseCitations(payload.citations)
  if (
    ordinal === null ||
    ordinal < 1 ||
    role === null ||
    invocationId === null ||
    taskId === null ||
    requestedModelId === null ||
    actualModelId === null ||
    actualModelId !== requestedModelId ||
    usage === null ||
    citations === null ||
    answerSha256 === null ||
    !SHA256.test(answerSha256)
  ) {
    return null
  }
  return {
    ordinal,
    role,
    invocationId,
    taskId,
    requestedModelId,
    actualModelId,
    usage,
    answerSha256,
    citations,
  }
}

function malformed(): P6PracticeTerminal {
  return { kind: 'error', code: 'p6_practice_stream_malformed' }
}

async function sha256Text(value: string): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(value))
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, '0')).join('')
}

export async function consumeP6PracticeStream(
  reader: ReadableStreamDefaultReader<Uint8Array>,
  callbacks: P6PracticeStreamCallbacks = {},
): Promise<P6PracticeTerminal> {
  const decoder = new TextDecoder()
  let buffer = ''
  let observedCharacters = 0
  let started: P6PracticeStarted | null = null
  let active: ActiveNode | null = null
  const nodes: P6PracticeNodeReceipt[] = []
  const invocationIds = new Set<string>()
  const taskIds = new Set<string>()
  let terminal: P6PracticeTerminal | null = null

  try {
    while (true) {
      const { done, value } = await reader.read()
      const decoded = done ? decoder.decode() : decoder.decode(value, { stream: true })
      observedCharacters += decoded.length
      if (observedCharacters > MAX_STREAM_CHARACTERS) return malformed()
      buffer += decoded
      let frames: ParsedFrame[]
      try {
        const parsed = takeFrames(buffer, done)
        frames = parsed[0]
        buffer = parsed[1]
      } catch {
        return malformed()
      }
      for (const frame of frames) {
        if (terminal !== null) {
          return { kind: 'error', code: 'p6_practice_stream_after_terminal' }
        }
        if (frame.event === 'practice_started') {
          if (started !== null || active !== null || nodes.length > 0) return malformed()
          started = parseStarted(frame.payload)
          if (started === null) return malformed()
          callbacks.onStarted?.(started)
        } else if (frame.event === 'node_started') {
          if (started === null || active !== null) return malformed()
          const ordinal = finiteInteger(frame.payload.ordinal)
          const role = practiceRole(frame.payload.role)
          if (
            ordinal === null ||
            ordinal !== nodes.length + 1 ||
            role === null ||
            started.roles[ordinal - 1] !== role
          ) {
            return malformed()
          }
          active = {
            ordinal,
            role,
            identity: null,
            usageObserved: false,
            citations: null,
          }
          callbacks.onNodeStarted?.({ ordinal, role })
        } else if (frame.event === 'node_event') {
          if (started === null || active === null) return malformed()
          const ordinal = finiteInteger(frame.payload.ordinal)
          const role = practiceRole(frame.payload.role)
          if (ordinal !== active.ordinal || role !== active.role) return malformed()
          if (frame.payload.event === 'meta') {
            if (active.identity !== null || active.citations !== null || active.usageObserved) {
              return malformed()
            }
            const invocationId = requiredString(frame.payload.invocation_id)
            const taskId = requiredString(frame.payload.task_id)
            const requestedModelId = requiredString(frame.payload.requested_model_id)
            if (
              invocationId === null ||
              taskId === null ||
              requestedModelId === null ||
              invocationIds.has(invocationId) ||
              taskIds.has(taskId)
            ) {
              return malformed()
            }
            invocationIds.add(invocationId)
            taskIds.add(taskId)
            active.identity = {
              invocationId,
              taskId,
              requestedModelId,
            }
            callbacks.onNodeIdentity?.({
              ordinal,
              role,
              invocationId,
              taskId,
              requestedModelId,
            })
          } else if (frame.payload.event === 'citations') {
            if (active.identity === null || active.citations !== null || active.usageObserved) {
              return malformed()
            }
            active.citations = parseCitations(frame.payload.citations)
            if (active.citations === null) return malformed()
          } else if (frame.payload.event === 'usage') {
            if (active.identity === null || active.citations === null || active.usageObserved) {
              return malformed()
            }
            active.usageObserved = true
          } else {
            return malformed()
          }
        } else if (frame.event === 'node_completed') {
          if (started === null || active === null) return malformed()
          const receipt = parseNodeReceipt(frame.payload)
          if (
            receipt === null ||
            receipt.ordinal !== active.ordinal ||
            receipt.role !== active.role ||
            active.identity === null ||
            !active.usageObserved ||
            active.citations === null ||
            receipt.invocationId !== active.identity.invocationId ||
            receipt.taskId !== active.identity.taskId ||
            receipt.requestedModelId !== active.identity.requestedModelId ||
            JSON.stringify(receipt.citations) !== JSON.stringify(active.citations)
          ) {
            return malformed()
          }
          nodes.push(receipt)
          active = null
          callbacks.onNodeCompleted?.(receipt)
        } else if (frame.event === 'practice_completed') {
          if (started === null || active !== null || nodes.length !== started.participantCount) {
            return malformed()
          }
          const scenario = frame.payload.scenario
          const participantCount = finiteInteger(frame.payload.participant_count)
          const providerCallCount = finiteInteger(frame.payload.provider_call_count)
          const parentInvocationId = requiredString(frame.payload.parent_invocation_id)
          const parentTaskId = requiredString(frame.payload.parent_task_id)
          const finalAnswer = requiredString(frame.payload.final_answer)
          const finalAnswerSha256 = requiredString(frame.payload.final_answer_sha256)
          const parent = nodes.at(-1)
          if (
            scenario !== started.scenario ||
            participantCount !== started.participantCount ||
            providerCallCount !== started.participantCount ||
            parent?.role !== 'parent' ||
            parentInvocationId !== parent.invocationId ||
            parentTaskId !== parent.taskId ||
            finalAnswerSha256 !== parent.answerSha256 ||
            finalAnswer === null ||
            finalAnswerSha256 === null ||
            !SHA256.test(finalAnswerSha256) ||
            (await sha256Text(finalAnswer)) !== finalAnswerSha256
          ) {
            return malformed()
          }
          terminal = {
            kind: 'done',
            scenario: started.scenario,
            participantCount: started.participantCount,
            providerCallCount,
            parentInvocationId,
            parentTaskId,
            finalAnswer,
            finalAnswerSha256,
            nodes: [...nodes],
          }
        } else if (frame.event === 'error') {
          terminal = {
            kind: 'error',
            code:
              typeof frame.payload.code === 'string'
                ? frame.payload.code
                : 'personal_practice_failed',
          }
        } else {
          return malformed()
        }
      }
      if (done) break
    }
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') {
      return { kind: 'cancelled' }
    }
    throw error
  }
  return terminal ?? { kind: 'error', code: 'p6_practice_stream_incomplete' }
}
