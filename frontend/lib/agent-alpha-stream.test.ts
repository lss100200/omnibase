import assert from 'node:assert/strict'
import { test } from 'node:test'

import {
  consumeAgentAlphaStream,
  formatAgentIdentity,
  takeAgentAlphaEvents,
} from './agent-alpha-stream'

function sseReader(frames: string[]): ReadableStreamDefaultReader<Uint8Array> {
  const encoder = new TextEncoder()
  const chunks = frames.map((frame) => encoder.encode(frame))
  const stream = new ReadableStream<Uint8Array>({
    pull(controller) {
      const chunk = chunks.shift()
      if (chunk === undefined) {
        controller.close()
      } else {
        controller.enqueue(chunk)
      }
    },
  })
  return stream.getReader()
}

function metaFrame(invocationId = 'inv-1'): string {
  return (
    'event: meta\n' +
    `data: ${JSON.stringify({ invocation_id: invocationId, task_id: 'task-1' })}\n\n`
  )
}

function chunkFrame(content: string): string {
  return `event: chunk\ndata: ${JSON.stringify({ content })}\n\n`
}

function doneFrame(answer = 'final answer'): string {
  return (
    'event: done\n' +
    `data: ${JSON.stringify({ answer, usage: { input_tokens: 1, output_tokens: 2, total_tokens: 3 } })}\n\n`
  )
}

function errorFrame(code = 'agent_alpha_provider_unavailable'): string {
  return `event: error\ndata: ${JSON.stringify({ code })}\n\n`
}

function cancelledFrame(): string {
  return 'event: cancelled\ndata: {"invocation_id":"inv-1"}\n\n'
}

test('delta + done produces a successful terminal', async () => {
  const result = await consumeAgentAlphaStream(
    sseReader([metaFrame(), chunkFrame('Hello '), chunkFrame('world'), doneFrame('Hello world')]),
  )
  assert.equal(result.kind, 'done')
  if (result.kind === 'done') {
    assert.equal(result.answer, 'Hello world')
    assert.equal(result.invocationId, 'inv-1')
    assert.equal(result.taskId, 'task-1')
    assert.equal(result.usage?.total_tokens, 3)
  }
})

test('done without delta still succeeds with the done answer', async () => {
  const result = await consumeAgentAlphaStream(sseReader([metaFrame(), doneFrame('direct')]))
  assert.equal(result.kind, 'done')
  if (result.kind === 'done') assert.equal(result.answer, 'direct')
})

test('EOF before any event is incomplete, never a success', async () => {
  const result = await consumeAgentAlphaStream(sseReader(['']))
  assert.deepEqual(result, { kind: 'error', code: 'agent_alpha_stream_incomplete' })
})

test('partial delta then EOF is incomplete', async () => {
  const result = await consumeAgentAlphaStream(sseReader([metaFrame(), chunkFrame('partial ')]))
  assert.deepEqual(result, { kind: 'error', code: 'agent_alpha_stream_incomplete' })
})

test('error event fails with the backend code', async () => {
  const result = await consumeAgentAlphaStream(
    sseReader([metaFrame(), chunkFrame('x'), errorFrame('agent_alpha_binding_not_found')]),
  )
  assert.deepEqual(result, { kind: 'error', code: 'agent_alpha_binding_not_found' })
})

test('cancelled event maps to user cancellation', async () => {
  const result = await consumeAgentAlphaStream(
    sseReader([metaFrame(), chunkFrame('x'), cancelledFrame()]),
  )
  assert.deepEqual(result, { kind: 'cancelled' })
})

test('fetch AbortError maps to user cancellation', async () => {
  const encoder = new TextEncoder()
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      controller.enqueue(encoder.encode(metaFrame() + chunkFrame('x')))
    },
    pull() {
      throw new DOMException('aborted', 'AbortError')
    },
  })
  const result = await consumeAgentAlphaStream(stream.getReader())
  assert.deepEqual(result, { kind: 'cancelled' })
})

test('malformed JSON fails closed', async () => {
  const result = await consumeAgentAlphaStream(sseReader(['event: chunk\ndata: {not json}\n\n']))
  assert.deepEqual(result, { kind: 'error', code: 'agent_alpha_stream_malformed' })
})

test('malformed terminal payload (done without answer) fails closed', async () => {
  const result = await consumeAgentAlphaStream(sseReader(['event: done\ndata: {"usage":{}}\n\n']))
  assert.deepEqual(result, { kind: 'error', code: 'agent_alpha_stream_malformed' })
})

test('split SSE frames across reads are reassembled', async () => {
  const meta = metaFrame()
  const half = Math.floor(meta.length / 2)
  const result = await consumeAgentAlphaStream(
    sseReader([meta.slice(0, half), meta.slice(half) + chunkFrame('a') + doneFrame('a')]),
  )
  assert.equal(result.kind, 'done')
  if (result.kind === 'done') {
    assert.equal(result.answer, 'a')
    assert.equal(result.invocationId, 'inv-1')
  }
})

test('multiple events in one chunk and residual buffer handling', async () => {
  const result = await consumeAgentAlphaStream(
    sseReader([metaFrame() + chunkFrame('a') + chunkFrame('b'), doneFrame('ab') + 'garbage\n']),
  )
  assert.equal(result.kind, 'done')
  if (result.kind === 'done') assert.equal(result.answer, 'ab')
})

test('duplicate terminal events fail closed', async () => {
  const result = await consumeAgentAlphaStream(
    sseReader([metaFrame(), doneFrame('one'), doneFrame('two')]),
  )
  assert.deepEqual(result, { kind: 'error', code: 'agent_alpha_stream_after_terminal' })
})

test('events after a terminal event fail closed', async () => {
  const result = await consumeAgentAlphaStream(
    sseReader([metaFrame(), doneFrame('one'), chunkFrame('extra')]),
  )
  assert.deepEqual(result, { kind: 'error', code: 'agent_alpha_stream_after_terminal' })
})

test('chunk callbacks stream live content', async () => {
  const chunks: string[] = []
  const result = await consumeAgentAlphaStream(
    sseReader([metaFrame(), chunkFrame('a'), chunkFrame('b'), doneFrame('ab')]),
    { onChunk: (content) => chunks.push(content) },
  )
  assert.deepEqual(chunks, ['a', 'b'])
  assert.equal(result.kind, 'done')
})

test('formatAgentIdentity uses actual model when present', () => {
  assert.equal(
    formatAgentIdentity({
      provider_id: 'fake',
      requested_model_id: 'req-model',
      credential_source: 'operator_default',
    }),
    'fake / req-model · operator_default',
  )
  assert.equal(
    formatAgentIdentity({
      provider_id: 'fake',
      actual_model_id: 'real-model',
      credential_source: 'operator_default',
    }),
    'fake / real-model (actual) · operator_default',
  )
})

test('takeAgentAlphaEvents recognizes the alpha vocabulary', () => {
  const [events] = takeAgentAlphaEvents(
    metaFrame() + chunkFrame('x') + cancelledFrame() + doneFrame('y'),
    true,
  )
  const kinds = events.map((event) => event.kind)
  assert.deepEqual(kinds, ['meta', 'chunk', 'cancelled', 'done'])
})
