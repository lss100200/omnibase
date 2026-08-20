import assert from 'node:assert/strict'
import { test } from 'node:test'

import {
  applyDesktopConversationEvent,
  beginDesktopLiveSend,
  completeDesktopLiveSend,
  createDesktopLiveStreamState,
  resolveDesktopBridge,
  switchDesktopLiveScope,
} from './desktop-bridge'

function bridgeFixture() {
  return {
    app: { getVersion: async () => '1.0.0' },
    runtime: {
      getStatus: async () => ({ phase: 'ready', attempts: 1, lastError: null }),
      retryStartup: async () => ({ phase: 'ready', attempts: 1, lastError: null }),
    },
    owner: {
      getStatus: async () => ({ ok: true, value: { initialized: false, owner: null } }),
      bootstrap: async () => ({ ok: false, error: { code: 'not-called' } }),
    },
    workspaces: {
      list: async () => ({ ok: true, value: { items: [] } }),
      create: async () => ({ ok: false, error: { code: 'not-called' } }),
      archive: async () => ({ ok: false, error: { code: 'not-called' } }),
      agent: async () => ({ ok: false, error: { code: 'not-called' } }),
    },
    providers: {
      list: async () => ({ ok: true, value: { items: [] } }),
      upsert: async () => ({ ok: false, error: { code: 'not-called' } }),
      delete: async () => ({ ok: false, error: { code: 'not-called' } }),
      test: async () => ({ ok: false, error: { code: 'not-called' } }),
    },
    conversations: {
      list: async () => ({ ok: true, value: { items: [] } }),
      create: async () => ({ ok: false, error: { code: 'not-called' } }),
      archive: async () => ({ ok: false, error: { code: 'not-called' } }),
      get: async () => ({ ok: false, error: { code: 'not-called' } }),
      send: async () => ({ ok: false, error: { code: 'not-called' } }),
      cancel: async () => ({ ok: false, error: { code: 'not-called' } }),
      abortInFlightSend: async () => ({ ok: false, error: { code: 'not-called' } }),
      subscribe: () => () => undefined,
    },
    agents: {
      roles: {
        list: async () => ({ ok: true, value: { items: [] } }),
        get: async () => ({ ok: false, error: { code: 'not-called' } }),
        update: async () => ({ ok: false, error: { code: 'not-called' } }),
        test: async () => ({ ok: false, error: { code: 'not-called' } }),
      },
    },
    teamRuns: {
      start: async () => ({ ok: false, error: { code: 'not-called' } }),
      cancel: async () => ({ ok: false, error: { code: 'not-called' } }),
      get: async () => ({ ok: false, error: { code: 'not-called' } }),
      list: async () => ({ ok: true, value: { items: [] } }),
      submitProposal: async () => ({ ok: false, error: { code: 'not-called' } }),
      getBlackboard: async () => ({ ok: false, error: { code: 'not-called' } }),
      recordCollaboration: async () => ({ ok: false, error: { code: 'not-called' } }),
      subscribe: () => () => undefined,
    },
  }
}

test('desktop bridge detection requires the complete closed product surface', () => {
  const complete = bridgeFixture()
  assert.equal(resolveDesktopBridge(complete), complete)
  assert.equal(resolveDesktopBridge(undefined), null)
  assert.equal(resolveDesktopBridge({}), null)
  assert.equal(
    resolveDesktopBridge({
      ...complete,
      workspaces: { ...complete.workspaces, archive: undefined },
    }),
    null,
  )
          assert.equal(
            resolveDesktopBridge({
              ...complete,
              owner: { ...complete.owner, bootstrap: 'not-a-function' },
            }),
            null,
          )
          assert.equal(
            resolveDesktopBridge({
              ...complete,
              conversations: { ...complete.conversations, subscribe: undefined },
            }),
            null,
          )
})

const WORKSPACE_A = `workspace_${'a'.repeat(32)}`
const WORKSPACE_B = `workspace_${'b'.repeat(32)}`
const CONVERSATION_A = `conversation_${'a'.repeat(32)}`
const CONVERSATION_B = `conversation_${'b'.repeat(32)}`
const INVOCATION_OLD = `invocation_${'1'.repeat(32)}`
const INVOCATION_NEW = `invocation_${'2'.repeat(32)}`

test('beginDesktopLiveSend refuses an in-flight invocation instead of replacing it', () => {
  const running = createDesktopLiveStreamState({
    workspaceId: WORKSPACE_A,
    conversationId: CONVERSATION_A,
    liveInvocation: INVOCATION_OLD,
    liveText: 'stale',
    liveMeta: {
      type: 'identity',
      invocationId: INVOCATION_OLD,
      workspaceId: WORKSPACE_A,
      conversationId: CONVERSATION_A,
    },
    streaming: true,
  })
  const refused = beginDesktopLiveSend(running)
  assert.equal(refused.liveInvocation, INVOCATION_OLD)
  assert.equal(refused.sendEpoch, running.sendEpoch)
  const idle = completeDesktopLiveSend(running, running.sendEpoch)
  const started = beginDesktopLiveSend(idle)
  assert.equal(started.liveInvocation, null)
  assert.equal(started.liveMeta, null)
  assert.equal(started.liveText, '')
  assert.equal(started.streaming, true)
  assert.equal(started.retiredInvocationIds.includes(INVOCATION_OLD), true)
})

test('cross-conversation deltas are dropped and other-scope streams are hidden', () => {
  let state = createDesktopLiveStreamState({
    workspaceId: WORKSPACE_A,
    conversationId: CONVERSATION_A,
    liveInvocation: INVOCATION_OLD,
    liveText: 'hello',
    liveMeta: {
      type: 'identity',
      invocationId: INVOCATION_OLD,
      workspaceId: WORKSPACE_A,
      conversationId: CONVERSATION_A,
    },
    streaming: true,
  })
  state = applyDesktopConversationEvent(state, {
    type: 'delta',
    invocationId: INVOCATION_OLD,
    workspaceId: WORKSPACE_A,
    conversationId: CONVERSATION_B,
    text: ' leaked',
    sendEpoch: state.sendEpoch,
  })
  assert.equal(state.liveText, 'hello')
  state = applyDesktopConversationEvent(state, {
    type: 'delta',
    invocationId: INVOCATION_NEW,
    workspaceId: WORKSPACE_A,
    conversationId: CONVERSATION_A,
    text: ' leaked-stale',
    sendEpoch: state.sendEpoch,
  })
  assert.equal(state.liveText, 'hello')
  state = applyDesktopConversationEvent(state, {
    type: 'delta',
    invocationId: INVOCATION_OLD,
    workspaceId: WORKSPACE_A,
    conversationId: CONVERSATION_A,
    text: ' world',
    sendEpoch: state.sendEpoch,
  })
  assert.equal(state.liveText, 'hello world')
  const switched = switchDesktopLiveScope(state, WORKSPACE_B, CONVERSATION_B)
  assert.equal(switched.liveText, '')
  assert.equal(switched.streaming, false)
  assert.equal(switched.liveInvocation, INVOCATION_OLD)
  const hiddenDelta = applyDesktopConversationEvent(switched, {
    type: 'delta',
    invocationId: INVOCATION_OLD,
    workspaceId: WORKSPACE_A,
    conversationId: CONVERSATION_A,
    text: ' hidden',
    sendEpoch: switched.sendEpoch,
  })
  assert.equal(hiddenDelta.liveText, '')
  const terminal = applyDesktopConversationEvent(hiddenDelta, {
    type: 'done',
    invocationId: INVOCATION_OLD,
    workspaceId: WORKSPACE_A,
    conversationId: CONVERSATION_A,
    status: 'succeeded',
    sendEpoch: hiddenDelta.sendEpoch,
  })
  assert.equal(terminal.liveInvocation, null)
  assert.equal(terminal.streaming, false)
})
