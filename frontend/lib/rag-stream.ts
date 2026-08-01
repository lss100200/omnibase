import type { Citation } from './types'

export type RagStreamEvent =
  | { readonly kind: 'chunk'; readonly content: string }
  | { readonly kind: 'citations'; readonly citations: readonly Citation[] }
  | {
      readonly kind: 'done'
      readonly answer: string | null
      readonly citations: readonly Citation[] | null
    }
  | { readonly kind: 'error'; readonly message: string | null }

interface SseFrame {
  readonly eventType: string
  readonly data: string
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null
}

function isCitation(value: unknown): value is Citation {
  if (!isRecord(value)) return false
  return (
    typeof value.index === 'number' &&
    typeof value.chunk_id === 'string' &&
    typeof value.document_id === 'string' &&
    typeof value.snippet === 'string' &&
    typeof value.page_number === 'number' &&
    typeof value.score === 'number'
  )
}

function parseCitations(value: unknown): readonly Citation[] | null {
  if (!Array.isArray(value) || !value.every(isCitation)) return null
  return value
}

function parseFrame(frame: SseFrame): RagStreamEvent | null {
  const data: unknown = JSON.parse(frame.data)
  if (!isRecord(data)) throw new Error('Invalid streamed response')

  switch (frame.eventType) {
    case 'chunk':
      if (typeof data.content !== 'string') throw new Error('Invalid streamed response')
      return { kind: 'chunk', content: data.content }
    case 'citations': {
      const citations = parseCitations(data.citations)
      if (!citations) throw new Error('Invalid streamed response')
      return { kind: 'citations', citations }
    }
    case 'done': {
      const answer = typeof data.answer === 'string' ? data.answer : null
      const citations = data.citations === undefined ? null : parseCitations(data.citations)
      if (data.citations !== undefined && !citations) throw new Error('Invalid streamed response')
      return { kind: 'done', answer, citations }
    }
    case 'error': {
      const message = typeof data.message === 'string' ? data.message : null
      return { kind: 'error', message }
    }
    default:
      return null
  }
}

export function takeRagStreamEvents(
  buffer: string,
  flush = false,
): readonly [readonly RagStreamEvent[], string] {
  const events: RagStreamEvent[] = []
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
      if (event) events.push(event)
    }
  }

  if (flush && remaining.trim()) {
    const [lastEvents] = takeRagStreamEvents(`${remaining}\n\n`)
    events.push(...lastEvents)
    remaining = ''
  }

  return [events, remaining]
}
