/**
 * Agent Alpha SSE consumption state machine (P5.4D Round 2 P1-4).
 *
 * A terminal `done` event is the ONLY way to produce a successful Agent
 * message.  Everything else fails closed:
 *
 * * EOF without any terminal event (empty stream, partial tokens then EOF,
 *   mid-stream drop) -> `agent_alpha_stream_incomplete`; an empty stream is
 *   never rendered as "No answer returned.";
 * * `error` event -> terminal error with the backend code;
 * * `cancelled` event -> user cancellation;
 * * fetch AbortError -> user cancellation (`cancelled`);
 * * malformed JSON / malformed terminal payload -> `agent_alpha_stream_malformed`;
 * * a second terminal event or any event AFTER a terminal event ->
 *   `agent_alpha_stream_after_terminal`.
 *
 * The UI error text is derived from stable codes; no backend URL, stack or
 * internal exception is ever rendered.  The frame parser is local because
 * the Agent Alpha event vocabulary (meta/citations/chunk/usage/done/error/
 * cancelled) is richer than the RAG stream vocabulary.
 */

export interface AgentAlphaStreamCallbacks {
  onMeta?: (payload: {
    invocationId: string | null
    taskId: string | null
    identity: string | null
    providerId: string | null
    requestedModelId: string | null
    actualModelId: string | null
  }) => void
  onCitations?: (citations: Citation[]) => void
  onChunk?: (content: string) => void
  onUsage?: (usage: AgentAlphaUsage | null) => void
}

export interface AgentAlphaUsage {
  input_tokens: number
  output_tokens: number
  total_tokens: number
  reasoning_tokens?: number
  cached_input_tokens?: number
  cache_miss_input_tokens?: number
}

export type AgentAlphaStreamTerminal =
  | {
      kind: 'done'
      answer: string
      citations: Citation[]
      invocationId: string | null
      taskId: string | null
      identity: string | null
      usage: AgentAlphaUsage | null
    }
  | { kind: 'cancelled' }
  | { kind: 'error'; code: string }

export interface Citation {
  index: number
  chunk_id: string
  document_id: string
  snippet: string
  page_number: number
  score: number
}

interface SseFrame {
  readonly eventType: string
  readonly data: string
}

type ParsedEvent =
  | { kind: 'meta'; payload: Record<string, unknown> }
  | { kind: 'citations'; payload: Record<string, unknown> }
  | { kind: 'chunk'; payload: Record<string, unknown> }
  | { kind: 'usage'; payload: Record<string, unknown> }
  | { kind: 'done'; payload: Record<string, unknown> }
  | { kind: 'error'; payload: Record<string, unknown> }
  | { kind: 'cancelled'; payload: Record<string, unknown> }
  | { kind: 'unknown' }

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null
}

function parseFrame(frame: SseFrame): ParsedEvent {
  let data: unknown
  try {
    data = JSON.parse(frame.data)
  } catch {
    throw new Error('malformed SSE data')
  }
  if (!isRecord(data)) throw new Error('malformed SSE payload')
  switch (frame.eventType) {
    case 'meta':
    case 'citations':
    case 'chunk':
    case 'usage':
    case 'done':
    case 'error':
    case 'cancelled':
      return { kind: frame.eventType, payload: data }
    default:
      return { kind: 'unknown' }
  }
}

export function takeAgentAlphaEvents(
  buffer: string,
  flush = false,
): readonly [readonly ParsedEvent[], string] {
  const events: ParsedEvent[] = []
  let remaining = buffer
  const boundary = /\r?\n\r?\n/

  while (true) {
    const match = boundary.exec(remaining)
    if (!match || match.index === undefined) break
    const rawFrame = remaining.slice(0, match.index)
    remaining = remaining.slice(match.index + match[0].length)
    const dataLines: string[] = []
    let eventType = 'message'

    for (const line of rawFrame.split(/\r?\n/)) {
      if (line.startsWith('event:')) {
        eventType = line.slice(6).trim()
      } else if (line.startsWith('data:')) {
        const data = line.slice(5)
        dataLines.push(data.startsWith(' ') ? data.slice(1) : data)
      }
    }

    if (dataLines.length > 0) {
      const event = parseFrame({ eventType, data: dataLines.join('\n') })
      if (event.kind !== 'unknown') events.push(event)
    }
  }

  if (flush && remaining.trim()) {
    const [lastEvents] = takeAgentAlphaEvents(`${remaining}\n\n`)
    events.push(...lastEvents)
  }
  return [events, remaining]
}

function parseCitations(value: unknown): Citation[] {
  if (!Array.isArray(value)) return []
  const citations: Citation[] = []
  for (const item of value) {
    if (!isRecord(item)) continue
    if (
      typeof item.index === 'number' &&
      typeof item.chunk_id === 'string' &&
      typeof item.document_id === 'string' &&
      typeof item.snippet === 'string'
    ) {
      citations.push({
        index: item.index,
        chunk_id: item.chunk_id,
        document_id: item.document_id,
        snippet: item.snippet,
        page_number: typeof item.page_number === 'number' ? item.page_number : 1,
        score: typeof item.score === 'number' ? item.score : 0,
      })
    }
  }
  return citations
}

function parseUsage(value: unknown): AgentAlphaUsage | null {
  if (!isRecord(value)) return null
  const input = value.input_tokens
  const output = value.output_tokens
  const total = value.total_tokens
  const reasoning = value.reasoning_tokens
  const cached = value.cached_input_tokens
  const cacheMiss = value.cache_miss_input_tokens
  if (
    typeof input !== 'number' ||
    typeof output !== 'number' ||
    typeof total !== 'number' ||
    !Number.isFinite(input) ||
    !Number.isFinite(output) ||
    !Number.isFinite(total) ||
    input < 0 ||
    output < 0 ||
    total < 0 ||
    (reasoning !== undefined &&
      (typeof reasoning !== 'number' || !Number.isFinite(reasoning) || reasoning < 0)) ||
    (cached !== undefined &&
      (typeof cached !== 'number' || !Number.isFinite(cached) || cached < 0)) ||
    (cacheMiss !== undefined &&
      (typeof cacheMiss !== 'number' || !Number.isFinite(cacheMiss) || cacheMiss < 0)) ||
    (typeof cached === 'number' && typeof cacheMiss === 'number' && cached + cacheMiss > input)
  ) {
    return null
  }
  return {
    input_tokens: input,
    output_tokens: output,
    total_tokens: total,
    ...(typeof reasoning === 'number' ? { reasoning_tokens: reasoning } : {}),
    ...(typeof cached === 'number' ? { cached_input_tokens: cached } : {}),
    ...(typeof cacheMiss === 'number' ? { cache_miss_input_tokens: cacheMiss } : {}),
  }
}

export async function consumeAgentAlphaStream(
  reader: ReadableStreamDefaultReader<Uint8Array>,
  callbacks: AgentAlphaStreamCallbacks = {},
): Promise<AgentAlphaStreamTerminal> {
  const decoder = new TextDecoder()
  let buffer = ''
  let invocationId: string | null = null
  let taskId: string | null = null
  let identity: string | null = null
  let providerId: string | null = null
  let requestedModelId: string | null = null
  let actualModelId: string | null = null
  let credentialSource: string | null = null
  let citations: Citation[] = []
  let usage: AgentAlphaUsage | null = null
  let terminal: AgentAlphaStreamTerminal | null = null

  try {
    while (true) {
      const { done, value } = await reader.read()
      buffer += done ? decoder.decode() : decoder.decode(value, { stream: true })
      let events: readonly ParsedEvent[]
      try {
        const [parsed, remaining] = takeAgentAlphaEvents(buffer, done)
        events = parsed
        buffer = remaining
      } catch {
        return { kind: 'error', code: 'agent_alpha_stream_malformed' }
      }
      for (const event of events) {
        // Any event AFTER a terminal event is a protocol violation.
        if (terminal !== null) {
          return { kind: 'error', code: 'agent_alpha_stream_after_terminal' }
        }
        switch (event.kind) {
          case 'meta': {
            invocationId =
              typeof event.payload.invocation_id === 'string' ? event.payload.invocation_id : null
            taskId = typeof event.payload.task_id === 'string' ? event.payload.task_id : null
            providerId =
              typeof event.payload.provider_id === 'string' ? event.payload.provider_id : null
            requestedModelId =
              typeof event.payload.requested_model_id === 'string'
                ? event.payload.requested_model_id
                : null
            actualModelId =
              typeof event.payload.actual_model_id === 'string'
                ? event.payload.actual_model_id
                : null
            credentialSource =
              typeof event.payload.credential_source === 'string'
                ? event.payload.credential_source
                : null
            identity = formatAgentIdentity(event.payload)
            callbacks.onMeta?.({
              invocationId,
              taskId,
              identity,
              providerId,
              requestedModelId,
              actualModelId,
            })
            break
          }
          case 'citations':
            citations = parseCitations(event.payload.citations)
            callbacks.onCitations?.(citations)
            break
          case 'chunk':
            if (typeof event.payload.content === 'string') {
              callbacks.onChunk?.(event.payload.content)
            }
            break
          case 'usage':
            usage = parseUsage(event.payload)
            callbacks.onUsage?.(usage)
            break
          case 'done': {
            // A terminal `done` payload must carry a string answer; a
            // missing/absent answer is malformed and never succeeds.
            if (typeof event.payload.answer !== 'string') {
              return { kind: 'error', code: 'agent_alpha_stream_malformed' }
            }
            const doneCitations =
              event.payload.citations === undefined
                ? citations
                : parseCitations(event.payload.citations)
            const doneUsage =
              event.payload.usage === undefined ? usage : parseUsage(event.payload.usage)
            actualModelId =
              typeof event.payload.actual_model_id === 'string'
                ? event.payload.actual_model_id
                : actualModelId
            if (actualModelId !== null) {
              identity = formatAgentIdentity({
                provider_id: providerId,
                requested_model_id: requestedModelId,
                actual_model_id: actualModelId,
                credential_source:
                  typeof event.payload.credential_source === 'string'
                    ? event.payload.credential_source
                    : credentialSource,
              })
              callbacks.onMeta?.({
                invocationId,
                taskId,
                identity,
                providerId,
                requestedModelId,
                actualModelId,
              })
            }
            terminal = {
              kind: 'done',
              answer: event.payload.answer,
              citations: doneCitations,
              invocationId,
              taskId,
              identity,
              usage: doneUsage,
            }
            break
          }
          case 'error':
            terminal = {
              kind: 'error',
              code:
                typeof event.payload.code === 'string' ? event.payload.code : 'agent_alpha_error',
            }
            break
          case 'cancelled':
            terminal = { kind: 'cancelled' }
            break
          default:
            break
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
  if (terminal !== null) return terminal
  // EOF without a terminal event: the stream is incomplete.  Partial tokens
  // and the empty stream both land here; neither may render as success.
  return { kind: 'error', code: 'agent_alpha_stream_incomplete' }
}

export interface AgentAlphaMetaPayload {
  invocation_id?: unknown
  task_id?: unknown
  provider_id?: unknown
  requested_model_id?: unknown
  credential_source?: unknown
  actual_model_id?: unknown
}

export function formatAgentIdentity(payload: AgentAlphaMetaPayload): string {
  const provider = typeof payload.provider_id === 'string' ? payload.provider_id : 'unknown'
  const model =
    typeof payload.actual_model_id === 'string'
      ? payload.actual_model_id
      : typeof payload.requested_model_id === 'string'
        ? payload.requested_model_id
        : 'unknown'
  const source =
    typeof payload.credential_source === 'string' ? payload.credential_source : 'operator_default'
  const actual = typeof payload.actual_model_id === 'string' ? ' (actual)' : ''
  return `${provider} / ${model}${actual} · ${source}`
}
