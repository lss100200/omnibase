import assert from 'node:assert/strict'
import { test } from 'node:test'

import {
  applyDesktopConversationEvent,
  beginDesktopLiveSend,
  completeDesktopLiveSend,
  createDesktopLiveStreamState,
  desktopLiveStopVisible,
  switchDesktopLiveScope,
} from './desktop-bridge'
import {
  advanceDesktopSurfaceScope,
  applyDesktopScopedProjection,
  createDesktopSurfaceScope,
  desktopSurfaceProjectionIsCurrent,
} from './desktop-surface-scope'

const WORKSPACE_A = `workspace_${'a'.repeat(32)}`
const WORKSPACE_B = `workspace_${'b'.repeat(32)}`
const CONVERSATION_A = `conversation_${'a'.repeat(32)}`
const CONVERSATION_B = `conversation_${'b'.repeat(32)}`
const INVOCATION_A = `invocation_${'1'.repeat(32)}`

test('returning to the original scope restores Stop and the running live invocation', () => {
  let live = beginDesktopLiveSend(
    createDesktopLiveStreamState({
      workspaceId: WORKSPACE_A,
      conversationId: CONVERSATION_A,
    }),
  )
  live = applyDesktopConversationEvent(live, {
    type: 'identity',
    invocationId: INVOCATION_A,
    workspaceId: WORKSPACE_A,
    conversationId: CONVERSATION_A,
    status: 'running',
  })
  live = applyDesktopConversationEvent(live, {
    type: 'delta',
    invocationId: INVOCATION_A,
    workspaceId: WORKSPACE_A,
    conversationId: CONVERSATION_A,
    text: 'hello',
  })
  assert.equal(desktopLiveStopVisible(live), true)
  assert.equal(live.liveInvocation, INVOCATION_A)
  assert.equal(live.streaming, true)

  const onB = switchDesktopLiveScope(live, WORKSPACE_B, CONVERSATION_B)
  assert.equal(desktopLiveStopVisible(onB), true)
  assert.equal(onB.liveInvocation, INVOCATION_A)
  assert.equal(onB.streaming, false)
  assert.equal(onB.liveText, '')

  const hiddenDelta = applyDesktopConversationEvent(onB, {
    type: 'delta',
    invocationId: INVOCATION_A,
    workspaceId: WORKSPACE_A,
    conversationId: CONVERSATION_A,
    text: ' world',
  })
  assert.equal(hiddenDelta.liveText, '')
  assert.equal(desktopLiveStopVisible(hiddenDelta), true)
  assert.equal(hiddenDelta.liveInvocation, INVOCATION_A)

  const backOnA = switchDesktopLiveScope(hiddenDelta, WORKSPACE_A, CONVERSATION_A)
  assert.equal(desktopLiveStopVisible(backOnA), true)
  assert.equal(backOnA.liveInvocation, INVOCATION_A)
  assert.equal(backOnA.streaming, true)
  assert.equal(backOnA.liveText, 'hello world')
  assert.equal(backOnA.liveMeta?.status, 'running')
})

test('stale send completion does not overwrite the current workspace', () => {
  const started = createDesktopSurfaceScope(WORKSPACE_A, CONVERSATION_A)
  const current = advanceDesktopSurfaceScope(started, WORKSPACE_B, CONVERSATION_B)
  assert.equal(desktopSurfaceProjectionIsCurrent(started, current), false)

  const messagesOnB = ['conversation B']
  const applied = applyDesktopScopedProjection(started, current, messagesOnB, ['stale A completion'])
  assert.deepEqual(applied, messagesOnB)

  let live = beginDesktopLiveSend(
    createDesktopLiveStreamState({
      workspaceId: WORKSPACE_A,
      conversationId: CONVERSATION_A,
    }),
  )
  const sendGeneration = live.sendGeneration
  live = applyDesktopConversationEvent(live, {
    type: 'identity',
    invocationId: INVOCATION_A,
    workspaceId: WORKSPACE_A,
    conversationId: CONVERSATION_A,
  })
  live = switchDesktopLiveScope(live, WORKSPACE_B, CONVERSATION_B)
  const completedAway = completeDesktopLiveSend(live, sendGeneration)
  assert.equal(completedAway.workspaceId, WORKSPACE_B)
  assert.equal(completedAway.conversationId, CONVERSATION_B)
  assert.equal(completedAway.liveInvocation, null)
  assert.equal(desktopLiveStopVisible(completedAway), false)
  assert.deepEqual(
    applyDesktopScopedProjection(started, current, { title: 'B' }, { title: 'A done' }),
    { title: 'B' },
  )
})

test('stale list detail does not overwrite the newly selected conversation', () => {
  let scope = createDesktopSurfaceScope(WORKSPACE_A, CONVERSATION_A)
  const listDetailForA = {
    conversationId: CONVERSATION_A,
    messages: ['old A detail'],
  }
  scope = advanceDesktopSurfaceScope(scope, WORKSPACE_A, CONVERSATION_B)
  const selected = {
    conversationId: CONVERSATION_B,
    messages: ['fresh B detail'],
  }
  assert.equal(desktopSurfaceProjectionIsCurrent(createDesktopSurfaceScope(WORKSPACE_A, CONVERSATION_A), scope), false)
  const applied = applyDesktopScopedProjection(
    { workspaceId: WORKSPACE_A, conversationId: CONVERSATION_A, generation: 0 },
    scope,
    selected,
    listDetailForA,
  )
  assert.equal(applied.conversationId, CONVERSATION_B)
  assert.deepEqual(applied.messages, ['fresh B detail'])

  const returnedToA = advanceDesktopSurfaceScope(scope, WORKSPACE_A, CONVERSATION_A)
  const afterReturn = applyDesktopScopedProjection(
    { workspaceId: WORKSPACE_A, conversationId: CONVERSATION_A, generation: 0 },
    returnedToA,
    { conversationId: CONVERSATION_A, messages: ['new A load'] },
    listDetailForA,
  )
  assert.deepEqual(afterReturn.messages, ['new A load'])
})
