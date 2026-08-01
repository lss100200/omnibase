import assert from 'node:assert/strict'
import test from 'node:test'
import { takeRagStreamEvents } from './rag-stream'

test('parses split frames and multi-line data fields', () => {
  // Given: the server splits a valid JSON payload and a following frame across reads.
  const firstRead =
    'event: chunk\ndata: {"content":\ndata: "你好"}\n\nevent: done\ndata: {"answer":"完'
  const secondRead = '整答案","citations":[]}\n\n'

  // When: the client buffers and processes both reads.
  const [firstEvents, remaining] = takeRagStreamEvents(firstRead)
  const [secondEvents, finalRemaining] = takeRagStreamEvents(remaining + secondRead)

  // Then: both events retain their payloads and no partial data remains.
  assert.deepEqual(firstEvents, [{ kind: 'chunk', content: '你好' }])
  assert.deepEqual(secondEvents, [{ kind: 'done', answer: '完整答案', citations: [] }])
  assert.equal(finalRemaining, '')
})

test('rejects malformed streamed payloads', () => {
  // Given: a complete SSE frame whose payload is not JSON.
  const stream = 'event: chunk\ndata: not-json\n\n'

  // When / Then: parsing fails instead of surfacing untrusted data to the UI.
  assert.throws(() => takeRagStreamEvents(stream), SyntaxError)
})

test('parses error event with message string', () => {
  // Given: a backend error SSE frame with a message field.
  const stream = 'event: error\ndata: {"message": "LLM timeout after 30s"}\n\n'

  // When: the client parses the frame.
  const [events, remaining] = takeRagStreamEvents(stream)

  // Then: the error kind carries the backend message for UI surfacing.
  assert.deepEqual(events, [{ kind: 'error', message: 'LLM timeout after 30s' }])
  assert.equal(remaining, '')
})

test('parses error event with non-string message as null', () => {
  // Given: an error frame whose message field is missing.
  const stream = 'event: error\ndata: {}\n\n'

  // When: the client parses the frame.
  const [events] = takeRagStreamEvents(stream)

  // Then: message is null — frontend supplies a default.
  assert.deepEqual(events, [{ kind: 'error', message: null }])
})

test('handles empty stream (no events from flush of whitespace)', () => {
  // Given: a stream with no complete SSE frames.
  const stream = ''

  // When / Then: no events, no remaining buffer.
  const [events, remaining] = takeRagStreamEvents(stream, true)
  assert.deepEqual(events, [])
  assert.equal(remaining, '')
})

test('parses CRLF frames and validated citations', () => {
  const stream =
    'event: citations\r\ndata: {"citations":[{"index":1,"chunk_id":"chunk-1","document_id":"doc-1","snippet":"source","page_number":2,"score":0.9}]}\r\n\r\n'

  const [events, remaining] = takeRagStreamEvents(stream)

  assert.deepEqual(events, [
    {
      kind: 'citations',
      citations: [
        {
          index: 1,
          chunk_id: 'chunk-1',
          document_id: 'doc-1',
          snippet: 'source',
          page_number: 2,
          score: 0.9,
        },
      ],
    },
  ])
  assert.equal(remaining, '')
})

test('flushes a final frame without a trailing blank line', () => {
  const stream = 'event: done\ndata: {"answer":"final","citations":[]}'

  const [events, remaining] = takeRagStreamEvents(stream, true)

  assert.deepEqual(events, [{ kind: 'done', answer: 'final', citations: [] }])
  assert.equal(remaining, '')
})

test('rejects invalid citation shapes', () => {
  const stream = 'event: citations\ndata: {"citations":[{"index":1}]}\n\n'

  assert.throws(() => takeRagStreamEvents(stream), /Invalid streamed response/)
})

test('retains an incomplete frame until the next read', () => {
  const partial = 'event: chunk\ndata: {"content":"part'

  const [events, remaining] = takeRagStreamEvents(partial)

  assert.deepEqual(events, [])
  assert.equal(remaining, partial)
})

test('ignores unknown event types without throwing', () => {
  // Given: a frame with an event type not in the known set.
  const stream = 'event: ping\ndata: {"seq": 1}\n\n'

  // When / Then: parseFrame returns null for unknown types — event is skipped.
  const [events] = takeRagStreamEvents(stream)
  assert.deepEqual(events, [])
})
