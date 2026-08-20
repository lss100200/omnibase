import assert from 'node:assert/strict'
import { test } from 'node:test'

import {
  applyDesktopConversationEvent,
  beginDesktopLiveSend,
  completeDesktopLiveSend,
  createDesktopLiveStreamState,
  desktopInvocationCanSend,
  desktopInvocationCancelTarget,
  desktopInvocationIsStopping,
  desktopInvocationLiveProjection,
  desktopInvocationNeedsStreamAbort,
  desktopLiveSendBlocked,
  desktopLiveStopVisible,
  markDesktopInvocationCancelDispatched,
  reduceDesktopInvocationEvent,
  requestDesktopLiveCancel,
  switchDesktopLiveScope,
} from './desktop-invocation-lifecycle'

const WORKSPACE_A = `workspace_${'a'.repeat(32)}`
const WORKSPACE_B = `workspace_${'b'.repeat(32)}`
const CONVERSATION_A = `conversation_${'a'.repeat(32)}`
const CONVERSATION_B = `conversation_${'b'.repeat(32)}`
const INVOCATION_OLD = `invocation_${'1'.repeat(32)}`
const INVOCATION_NEW = `invocation_${'2'.repeat(32)}`

function identityEvent(
  invocationId: string,
  workspaceId = WORKSPACE_A,
  conversationId = CONVERSATION_A,
  sendEpoch?: number,
) {
  return {
    type: 'identity' as const,
    invocationId,
    workspaceId,
    conversationId,
    providerName: 'loopback',
    requestedModel: 'fake-model',
    status: 'running',
    ...(sendEpoch === undefined ? {} : { sendEpoch }),
  }
}

function identityFor(
  state: ReturnType<typeof createDesktopLiveStreamState>,
  invocationId: string,
) {
  return identityEvent(
    invocationId,
    state.originWorkspaceId ?? WORKSPACE_A,
    state.originConversationId ?? CONVERSATION_A,
    state.sendEpoch,
  )
}

function deltaEvent(
  invocationId: string,
  text: string,
  workspaceId = WORKSPACE_A,
  conversationId = CONVERSATION_A,
) {
  return {
    type: 'delta' as const,
    invocationId,
    workspaceId,
    conversationId,
    text,
  }
}

function terminalEvent(
  invocationId: string,
  type: 'done' | 'cancelled' | 'error' = 'done',
  status = 'succeeded',
  workspaceId = WORKSPACE_A,
  conversationId = CONVERSATION_A,
) {
  return {
    type,
    invocationId,
    workspaceId,
    conversationId,
    status,
  }
}

function startSend(workspaceId = WORKSPACE_A, conversationId = CONVERSATION_A) {
  return beginDesktopLiveSend(
    createDesktopLiveStreamState({
      workspaceId,
      conversationId,
    }),
  )
}

test('P6.8-A1 send then Stop before identity then identity cancels exactly once', () => {
  let state = startSend()
  assert.equal(state.phase, 'starting_identity')
  assert.equal(state.invocationId, null)
  assert.equal(state.originWorkspaceId, WORKSPACE_A)
  assert.equal(state.originConversationId, CONVERSATION_A)
  assert.equal(desktopLiveSendBlocked(state), true)
  assert.equal(desktopInvocationCanSend(state), false)
  assert.equal(desktopLiveStopVisible(state), true)

  state = requestDesktopLiveCancel(state)
  assert.equal(state.phase, 'cancelling')
  assert.equal(state.cancelRequested, true)
  assert.equal(state.cancelDispatched, false)
  assert.equal(desktopInvocationCanSend(state), false)
  assert.equal(desktopLiveSendBlocked(state), true)
  assert.equal(desktopInvocationIsStopping(state), true)
  assert.equal(desktopInvocationCancelTarget(state), null)
  assert.equal(desktopInvocationNeedsStreamAbort(state), true)

  const first = reduceDesktopInvocationEvent(state, identityFor(state, INVOCATION_NEW))
  assert.equal(first.cancelInvocationId, INVOCATION_NEW)
  assert.equal(first.state.invocationId, INVOCATION_NEW)
  assert.equal(first.state.cancelDispatched, true)
  assert.equal(first.state.phase, 'cancelling')
  assert.equal(desktopInvocationCanSend(first.state), false)
  assert.equal(desktopInvocationNeedsStreamAbort(first.state), false)

  const duplicate = reduceDesktopInvocationEvent(first.state, identityFor(first.state, INVOCATION_NEW))
  assert.equal(duplicate.cancelInvocationId, null)
  assert.equal(duplicate.state.cancelDispatched, true)

  const cancelled = applyDesktopConversationEvent(
    first.state,
    terminalEvent(INVOCATION_NEW, 'cancelled', 'cancelled'),
  )
  assert.equal(cancelled.terminalStatus, 'cancelled')
  assert.equal(desktopInvocationCanSend(cancelled), false)

  const idle = completeDesktopLiveSend(cancelled, cancelled.sendEpoch)
  assert.equal(idle.phase, 'idle')
  assert.equal(desktopInvocationCanSend(idle), true)
})

test('P6.8-A2 send then switch B then Stop before identity still cancels A once', () => {
  let state = startSend()
  const originWorkspace = state.originWorkspaceId
  const originConversation = state.originConversationId
  state = switchDesktopLiveScope(state, WORKSPACE_B, CONVERSATION_B)
  assert.equal(state.originWorkspaceId, originWorkspace)
  assert.equal(state.originConversationId, originConversation)
  assert.equal(state.workspaceId, WORKSPACE_B)
  state = requestDesktopLiveCancel(state)
  assert.equal(state.phase, 'cancelling')
  assert.equal(desktopInvocationCanSend(state), false)

  const reduced = reduceDesktopInvocationEvent(state, identityFor(state, INVOCATION_NEW))
  assert.equal(reduced.cancelInvocationId, INVOCATION_NEW)
  assert.equal(reduced.state.originWorkspaceId, WORKSPACE_A)
  assert.equal(reduced.state.originConversationId, CONVERSATION_A)
  assert.equal(reduced.state.workspaceId, WORKSPACE_B)
  assert.equal(
    reduceDesktopInvocationEvent(reduced.state, identityFor(reduced.state, INVOCATION_NEW)).cancelInvocationId,
    null,
  )
})

test('P6.8-A3 send then Stop then switch B still cancels A once', () => {
  let state = startSend()
  state = requestDesktopLiveCancel(state)
  state = switchDesktopLiveScope(state, WORKSPACE_B, CONVERSATION_B)
  assert.equal(state.originWorkspaceId, WORKSPACE_A)
  assert.equal(state.cancelRequested, true)
  assert.equal(desktopInvocationCanSend(state), false)
  const reduced = reduceDesktopInvocationEvent(state, identityFor(state, INVOCATION_NEW))
  assert.equal(reduced.cancelInvocationId, INVOCATION_NEW)
  assert.equal(reduced.state.phase, 'cancelling')
})

test('P6.8-A4 Send and Retry stay unavailable while cancel is pending', () => {
  let state = startSend()
  state = requestDesktopLiveCancel(state)
  assert.equal(desktopInvocationCanSend(state), false)
  assert.equal(desktopLiveSendBlocked(state), true)
  const bound = reduceDesktopInvocationEvent(state, identityFor(state, INVOCATION_NEW)).state
  assert.equal(desktopInvocationCanSend(bound), false)
  const afterDispatch = markDesktopInvocationCancelDispatched(bound)
  assert.equal(desktopInvocationCanSend(afterDispatch), false)
  const afterTerminal = applyDesktopConversationEvent(
    afterDispatch,
    terminalEvent(INVOCATION_NEW, 'cancelled', 'cancelled'),
  )
  assert.equal(desktopInvocationCanSend(afterTerminal), false)
  const secondSend = beginDesktopLiveSend(afterTerminal)
  assert.equal(secondSend.sendEpoch, afterTerminal.sendEpoch)
  assert.equal(secondSend.phase, afterTerminal.phase)
})

test('P6.8-A5 old terminal cannot end a new send that has no identity yet', () => {
  let state = startSend()
  state = applyDesktopConversationEvent(state, identityFor(state, INVOCATION_OLD))
  state = completeDesktopLiveSend(state, state.sendEpoch)
  state = beginDesktopLiveSend(state)
  assert.equal(state.phase, 'starting_identity')
  assert.equal(state.invocationId, null)
  const afterOldTerminal = applyDesktopConversationEvent(
    state,
    terminalEvent(INVOCATION_OLD, 'done', 'succeeded'),
  )
  assert.equal(afterOldTerminal.phase, 'starting_identity')
  assert.equal(afterOldTerminal.invocationId, null)
  assert.equal(desktopLiveSendBlocked(afterOldTerminal), true)
  assert.equal(afterOldTerminal.retiredInvocationIds.includes(INVOCATION_OLD), true)
})

test('P6.8-A6 old identity cannot bind to a new send', () => {
  let state = startSend()
  const firstEpoch = state.sendEpoch
  state = applyDesktopConversationEvent(state, identityFor(state, INVOCATION_OLD))
  state = completeDesktopLiveSend(state, state.sendEpoch)
  state = beginDesktopLiveSend(state)
  const afterOldIdentity = applyDesktopConversationEvent(
    state,
    identityEvent(INVOCATION_OLD, WORKSPACE_A, CONVERSATION_A, firstEpoch),
  )
  assert.equal(afterOldIdentity.invocationId, null)
  assert.equal(afterOldIdentity.phase, 'starting_identity')
  const bound = applyDesktopConversationEvent(afterOldIdentity, identityFor(afterOldIdentity, INVOCATION_NEW))
  assert.equal(bound.invocationId, INVOCATION_NEW)
})

test('P6.8-A7 late delta after current terminal leaves transcript unchanged', () => {
  let state = startSend()
  state = applyDesktopConversationEvent(state, identityFor(state, INVOCATION_NEW))
  state = applyDesktopConversationEvent(state, deltaEvent(INVOCATION_NEW, 'hello'))
  assert.equal(state.liveText, 'hello')
  state = applyDesktopConversationEvent(state, terminalEvent(INVOCATION_NEW, 'done', 'succeeded'))
  const afterLate = applyDesktopConversationEvent(state, deltaEvent(INVOCATION_NEW, ' leaked'))
  assert.equal(afterLate.liveText, 'hello')
  assert.equal(afterLate.phase, 'convergence')
})

test('P6.8-A8 duplicate identity or terminal neither recancels nor resurrects', () => {
  let state = startSend()
  state = requestDesktopLiveCancel(state)
  const first = reduceDesktopInvocationEvent(state, identityFor(state, INVOCATION_NEW))
  assert.equal(first.cancelInvocationId, INVOCATION_NEW)
  const secondIdentity = reduceDesktopInvocationEvent(first.state, identityFor(first.state, INVOCATION_NEW))
  assert.equal(secondIdentity.cancelInvocationId, null)
  let next = applyDesktopConversationEvent(
    first.state,
    terminalEvent(INVOCATION_NEW, 'cancelled', 'cancelled'),
  )
  const before = next.phase
  next = applyDesktopConversationEvent(next, terminalEvent(INVOCATION_NEW, 'cancelled', 'cancelled'))
  assert.equal(next.phase, before)
  next = applyDesktopConversationEvent(next, identityFor(next, INVOCATION_NEW))
  assert.notEqual(next.phase, 'running')
  assert.equal(next.invocationId, null)
})

test('P6.8-A9 late success after accepted cancel must not display succeeded', () => {
  let state = startSend()
  state = applyDesktopConversationEvent(state, identityFor(state, INVOCATION_NEW))
  state = requestDesktopLiveCancel(state)
  state = markDesktopInvocationCancelDispatched(state)
  state = applyDesktopConversationEvent(state, terminalEvent(INVOCATION_NEW, 'done', 'succeeded'))
  assert.equal(state.terminalStatus, 'cancelled')
  assert.equal(state.liveMeta?.status, 'cancelled')
  assert.notEqual(state.liveMeta?.status, 'succeeded')
  const lateSuccess = applyDesktopConversationEvent(
    state,
    terminalEvent(INVOCATION_NEW, 'done', 'succeeded'),
  )
  assert.equal(lateSuccess.terminalStatus, 'cancelled')
  assert.equal(lateSuccess.liveMeta?.status, 'cancelled')
})

test('P6.8-A10 send Promise failure with no identity returns to idle', () => {
  const started = startSend()
  assert.equal(started.invocationId, null)
  const idle = completeDesktopLiveSend(started, started.sendEpoch)
  assert.equal(idle.phase, 'idle')
  assert.equal(idle.invocationId, null)
  assert.equal(desktopInvocationCanSend(idle), true)
  assert.equal(desktopLiveSendBlocked(idle), false)
})

test('P6.8-A live projection compares origin to the current view, not parked flags', () => {
  let state = startSend()
  state = applyDesktopConversationEvent(state, identityFor(state, INVOCATION_NEW))
  state = applyDesktopConversationEvent(state, deltaEvent(INVOCATION_NEW, 'origin-text'))
  const onB = desktopInvocationLiveProjection(state, WORKSPACE_B, CONVERSATION_B)
  assert.equal(onB.visible, false)
  assert.equal(onB.liveText, '')
  assert.equal(onB.liveMeta, null)
  const onA = desktopInvocationLiveProjection(state, WORKSPACE_A, CONVERSATION_A)
  assert.equal(onA.visible, true)
  assert.equal(onA.liveText, 'origin-text')
  assert.equal(onA.liveMeta?.providerName, 'loopback')
})

test('P6.8-A omitted sendEpoch identity cannot bind a newer pending send', () => {
  const state = startSend()
  const omitted = applyDesktopConversationEvent(state, identityEvent(INVOCATION_OLD))
  assert.equal(omitted.invocationId, null)
  assert.equal(omitted.phase, 'starting_identity')
  assert.equal(desktopLiveSendBlocked(omitted), true)
  const bound = applyDesktopConversationEvent(omitted, identityFor(omitted, INVOCATION_NEW))
  assert.equal(bound.invocationId, INVOCATION_NEW)
})

test('P6.8-A unbound complete then late identity cannot bind send 2', () => {
  const first = startSend()
  const idle = completeDesktopLiveSend(first, first.sendEpoch)
  assert.equal(idle.phase, 'idle')
  assert.equal(idle.retiredSendEpochs.includes(first.sendEpoch), true)
  const second = beginDesktopLiveSend(idle)
  assert.equal(second.phase, 'starting_identity')
  assert.notEqual(second.sendEpoch, first.sendEpoch)
  const lateOmitted = applyDesktopConversationEvent(second, identityEvent(INVOCATION_OLD))
  assert.equal(lateOmitted.invocationId, null)
  assert.equal(lateOmitted.phase, 'starting_identity')
  const lateOldEpoch = applyDesktopConversationEvent(
    second,
    identityEvent(INVOCATION_OLD, WORKSPACE_A, CONVERSATION_A, first.sendEpoch),
  )
  assert.equal(lateOldEpoch.invocationId, null)
  assert.equal(lateOldEpoch.phase, 'starting_identity')
  const bound = applyDesktopConversationEvent(lateOldEpoch, identityFor(lateOldEpoch, INVOCATION_NEW))
  assert.equal(bound.invocationId, INVOCATION_NEW)
})

test('P6.8-A Stop before identity then aborted send Promise returns to idle', () => {
  let state = startSend()
  state = requestDesktopLiveCancel(state)
  assert.equal(desktopInvocationNeedsStreamAbort(state), true)
  assert.equal(desktopLiveSendBlocked(state), true)
  const idle = completeDesktopLiveSend(state, state.sendEpoch)
  assert.equal(idle.phase, 'idle')
  assert.equal(idle.terminalStatus, 'cancelled')
  assert.equal(desktopInvocationCanSend(idle), true)
  assert.equal(desktopInvocationNeedsStreamAbort(idle), false)
})

test('P6.8-A identity after unbound Stop still cancels exactly once', () => {
  let state = startSend()
  const originEpoch = state.sendEpoch
  state = requestDesktopLiveCancel(state)
  assert.equal(desktopInvocationNeedsStreamAbort(state), true)
  const idle = completeDesktopLiveSend(state, originEpoch)
  const late = reduceDesktopInvocationEvent(
    idle,
    identityEvent(INVOCATION_NEW, WORKSPACE_A, CONVERSATION_A, originEpoch),
  )
  assert.equal(late.cancelInvocationId, INVOCATION_NEW)
  assert.equal(late.state.invocationId, null)
  assert.equal(late.state.phase, 'idle')
  const duplicate = reduceDesktopInvocationEvent(
    late.state,
    identityEvent(INVOCATION_NEW, WORKSPACE_A, CONVERSATION_A, originEpoch),
  )
  assert.equal(duplicate.cancelInvocationId, null)
})

test('P6.8-A beginDesktopLiveSend refuses starting_identity and running', () => {
  const starting = startSend()
  const refusedStarting = beginDesktopLiveSend(starting)
  assert.equal(refusedStarting.sendEpoch, starting.sendEpoch)
  assert.equal(refusedStarting.phase, 'starting_identity')
  const running = applyDesktopConversationEvent(starting, identityFor(starting, INVOCATION_NEW))
  const refusedRunning = beginDesktopLiveSend(running)
  assert.equal(refusedRunning.sendEpoch, running.sendEpoch)
  assert.equal(refusedRunning.invocationId, INVOCATION_NEW)
  assert.equal(desktopLiveSendBlocked(refusedRunning), true)
})
