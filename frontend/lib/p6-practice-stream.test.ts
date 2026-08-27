import assert from 'node:assert/strict'
import { test } from 'node:test'

import { consumeP6PracticeStream, type P6PracticeNodeReceipt } from './p6-practice-stream'

function frame(event: string, payload: object): string {
  return `event: ${event}\ndata: ${JSON.stringify(payload)}\n\n`
}

function readerOf(parts: string[]): ReadableStreamDefaultReader<Uint8Array> {
  const encoder = new TextEncoder()
  const values = parts.map((part) => encoder.encode(part))
  return new ReadableStream<Uint8Array>({
    pull(controller) {
      const value = values.shift()
      if (value) controller.enqueue(value)
      else controller.close()
    },
  }).getReader()
}

const usage = {
  input_tokens: 10,
  output_tokens: 5,
  total_tokens: 15,
  reasoning_tokens: 1,
  cached_input_tokens: 2,
  cache_miss_input_tokens: 8,
}

function nodeFrames(ordinal: number, role: string, answerSha256 = 'a'.repeat(64)): string {
  const invocationId = `inv-${ordinal}`
  const taskId = `task-${ordinal}`
  return (
    frame('node_started', { ordinal, role }) +
    frame('node_event', {
      ordinal,
      role,
      event: 'meta',
      invocation_id: invocationId,
      task_id: taskId,
      requested_model_id: 'deepseek-v4-flash',
    }) +
    frame('node_event', {
      ordinal,
      role,
      event: 'citations',
      citations: [
        {
          index: 1,
          chunk_id: `chunk-${ordinal}`,
          document_id: `document-${ordinal}`,
          page_number: 1,
        },
      ],
    }) +
    frame('node_event', { ordinal, role, event: 'usage' }) +
    frame('node_completed', {
      ordinal,
      role,
      invocation_id: invocationId,
      task_id: taskId,
      requested_model_id: 'deepseek-v4-flash',
      actual_model_id: 'deepseek-v4-flash',
      usage,
      answer_sha256: answerSha256,
      citations: [
        {
          index: 1,
          chunk_id: `chunk-${ordinal}`,
          document_id: `document-${ordinal}`,
          page_number: 1,
        },
      ],
    })
  )
}

function completeThree(): string {
  const finalAnswer = '{"answer":"ORCHID-417"}'
  const finalAnswerSha256 = '393eebd1e91aecb0fb685d4ca64d5b941234f668162a00fdb593e73c9ea77a32'
  return (
    frame('practice_started', {
      scenario: 'rag',
      participant_count: 3,
      roles: ['data', 'qa', 'parent'],
      serial: true,
      enterprise_multi_agent: false,
    }) +
    nodeFrames(1, 'data') +
    nodeFrames(2, 'qa') +
    nodeFrames(3, 'parent', finalAnswerSha256) +
    frame('practice_completed', {
      scenario: 'rag',
      participant_count: 3,
      provider_call_count: 3,
      parent_invocation_id: 'inv-3',
      parent_task_id: 'task-3',
      final_answer: finalAnswer,
      final_answer_sha256: finalAnswerSha256,
    })
  )
}

test('three-Agent stream proves ordered independent node receipts', async () => {
  const receipts: P6PracticeNodeReceipt[] = []
  const identities: string[] = []
  const value = completeThree()
  const midpoint = Math.floor(value.length / 2)
  const result = await consumeP6PracticeStream(
    readerOf([value.slice(0, midpoint), value.slice(midpoint)]),
    {
      onNodeIdentity: (identity) => identities.push(identity.invocationId),
      onNodeCompleted: (receipt) => receipts.push(receipt),
    },
  )

  assert.equal(result.kind, 'done')
  if (result.kind === 'done') {
    assert.equal(result.participantCount, 3)
    assert.equal(result.providerCallCount, 3)
    assert.equal(result.parentInvocationId, 'inv-3')
    assert.equal(result.parentTaskId, 'task-3')
    assert.equal(result.nodes.length, 3)
  }
  assert.deepEqual(identities, ['inv-1', 'inv-2', 'inv-3'])
  assert.deepEqual(
    receipts.map((receipt) => receipt.role),
    ['data', 'qa', 'parent'],
  )
})

test('EOF before practice_completed is incomplete', async () => {
  const result = await consumeP6PracticeStream(
    readerOf([
      frame('practice_started', {
        scenario: 'rag',
        participant_count: 1,
        roles: ['parent'],
        serial: true,
        enterprise_multi_agent: false,
      }) + nodeFrames(1, 'parent'),
    ]),
  )
  assert.deepEqual(result, { kind: 'error', code: 'p6_practice_stream_incomplete' })
})

test('model identity drift is malformed', async () => {
  const invalid = completeThree().replace(
    '"actual_model_id":"deepseek-v4-flash"',
    '"actual_model_id":"deepseek-v4-pro"',
  )
  const result = await consumeP6PracticeStream(readerOf([invalid]))
  assert.deepEqual(result, { kind: 'error', code: 'p6_practice_stream_malformed' })
})

test('node meta identity drift from the terminal receipt is malformed', async () => {
  const invalid = completeThree().replace('"task_id":"task-1"', '"task_id":"task-drift"')
  const result = await consumeP6PracticeStream(readerOf([invalid]))
  assert.deepEqual(result, { kind: 'error', code: 'p6_practice_stream_malformed' })
})

test('duplicate node metadata is malformed', async () => {
  const duplicateMeta = frame('node_event', {
    ordinal: 1,
    role: 'data',
    event: 'meta',
    invocation_id: 'inv-1',
    task_id: 'task-1',
    requested_model_id: 'deepseek-v4-flash',
  })
  const invalid = completeThree().replace(
    frame('node_event', { ordinal: 1, role: 'data', event: 'usage' }),
    duplicateMeta + frame('node_event', { ordinal: 1, role: 'data', event: 'usage' }),
  )
  const result = await consumeP6PracticeStream(readerOf([invalid]))
  assert.deepEqual(result, { kind: 'error', code: 'p6_practice_stream_malformed' })
})

test('citations before node metadata are malformed', async () => {
  const meta = frame('node_event', {
    ordinal: 1,
    role: 'data',
    event: 'meta',
    invocation_id: 'inv-1',
    task_id: 'task-1',
    requested_model_id: 'deepseek-v4-flash',
  })
  const citations = frame('node_event', {
    ordinal: 1,
    role: 'data',
    event: 'citations',
    citations: [{ index: 1, chunk_id: 'chunk-1', document_id: 'document-1', page_number: 1 }],
  })
  const invalid = completeThree().replace(meta + citations, citations + meta)
  const result = await consumeP6PracticeStream(readerOf([invalid]))
  assert.deepEqual(result, { kind: 'error', code: 'p6_practice_stream_malformed' })
})

test('usage before citations is malformed', async () => {
  const citations = frame('node_event', {
    ordinal: 1,
    role: 'data',
    event: 'citations',
    citations: [{ index: 1, chunk_id: 'chunk-1', document_id: 'document-1', page_number: 1 }],
  })
  const usageEvent = frame('node_event', { ordinal: 1, role: 'data', event: 'usage' })
  const invalid = completeThree().replace(citations + usageEvent, usageEvent + citations)
  const result = await consumeP6PracticeStream(readerOf([invalid]))
  assert.deepEqual(result, { kind: 'error', code: 'p6_practice_stream_malformed' })
})

test('cross-node invocation or task identity reuse is malformed', async () => {
  const invalid = completeThree()
    .replace('"invocation_id":"inv-2"', '"invocation_id":"inv-1"')
    .replace('"task_id":"task-2"', '"task_id":"task-1"')
  const result = await consumeP6PracticeStream(readerOf([invalid]))
  assert.deepEqual(result, { kind: 'error', code: 'p6_practice_stream_malformed' })
})

test('inconsistent node token totals are malformed', async () => {
  const invalid = completeThree().replace('"total_tokens":15', '"total_tokens":14')
  const result = await consumeP6PracticeStream(readerOf([invalid]))
  assert.deepEqual(result, { kind: 'error', code: 'p6_practice_stream_malformed' })
})

test('citation receipt drift from the streamed citation map is malformed', async () => {
  const invalid = completeThree().replace('"chunk_id":"chunk-1"', '"chunk_id":"chunk-drift"')
  const result = await consumeP6PracticeStream(readerOf([invalid]))
  assert.deepEqual(result, { kind: 'error', code: 'p6_practice_stream_malformed' })
})

test('final answer digest mismatch is malformed', async () => {
  const invalid = completeThree().replace('ORCHID-417', 'ORCHID-418')
  const result = await consumeP6PracticeStream(readerOf([invalid]))
  assert.deepEqual(result, { kind: 'error', code: 'p6_practice_stream_malformed' })
})

test('out-of-order role is malformed', async () => {
  const invalid = completeThree().replace('"ordinal":1,"role":"data"', '"ordinal":1,"role":"qa"')
  const result = await consumeP6PracticeStream(readerOf([invalid]))
  assert.deepEqual(result, { kind: 'error', code: 'p6_practice_stream_malformed' })
})

test('events after completion fail closed', async () => {
  const result = await consumeP6PracticeStream(
    readerOf([completeThree() + frame('error', { code: 'late' })]),
  )
  assert.deepEqual(result, { kind: 'error', code: 'p6_practice_stream_after_terminal' })
})

test('backend error remains terminal and never becomes success', async () => {
  const result = await consumeP6PracticeStream(
    readerOf([
      frame('practice_started', {
        scenario: 'rag',
        participant_count: 3,
        roles: ['data', 'qa', 'parent'],
        serial: true,
        enterprise_multi_agent: false,
      }) +
        nodeFrames(1, 'data') +
        frame('node_started', { ordinal: 2, role: 'qa' }) +
        frame('error', { code: 'practice_node_terminal_failure:qa:unknown' }),
    ]),
  )
  assert.deepEqual(result, {
    kind: 'error',
    code: 'practice_node_terminal_failure:qa:unknown',
  })
})

test('fetch AbortError maps to cancelled', async () => {
  const stream = new ReadableStream<Uint8Array>({
    pull() {
      throw new DOMException('aborted', 'AbortError')
    },
  })
  const result = await consumeP6PracticeStream(stream.getReader())
  assert.deepEqual(result, { kind: 'cancelled' })
})
