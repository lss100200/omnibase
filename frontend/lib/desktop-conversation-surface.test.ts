import assert from 'node:assert/strict'
import { test } from 'node:test'

import {
  applyDesktopConversationArchive,
  applyDesktopConversationCompletion,
  applyDesktopConversationCreate,
  applyDesktopConversationDetail,
  applyDesktopWorkspaceLoad,
  beginDesktopSurfaceDetailRequest,
  beginDesktopSurfaceMutation,
  beginDesktopSurfaceWorkspaceLoad,
  createDesktopConversationSurface,
  selectDesktopConversation,
  unmountDesktopConversationSurface,
} from './desktop-conversation-surface'
import {
  applyDesktopConversationEvent,
  beginDesktopLiveSend,
  createDesktopLiveStreamState,
  desktopInvocationLiveProjection,
} from './desktop-invocation-lifecycle'

const WORKSPACE_A = `workspace_${'a'.repeat(32)}`
const WORKSPACE_B = `workspace_${'b'.repeat(32)}`
const CONVERSATION_A = `conversation_${'a'.repeat(32)}`
const CONVERSATION_B = `conversation_${'b'.repeat(32)}`
const INVOCATION_A = `invocation_${'1'.repeat(32)}`

test('P6.8-B1 A to B hides A transcript immediately while B detail is pending', () => {
  let surface = createDesktopConversationSurface<string, { id: string }>(WORKSPACE_A, CONVERSATION_A)
  surface = applyDesktopConversationDetail(surface, surface.detailRequestEpoch, CONVERSATION_A, {
    ok: true,
    messages: ['transcript A'],
  })
  assert.deepEqual(surface.messages, ['transcript A'])
  surface = selectDesktopConversation(surface, WORKSPACE_A, CONVERSATION_B)
  assert.equal(surface.conversationId, CONVERSATION_B)
  assert.deepEqual(surface.messages, [])
  assert.equal(surface.messagesStatus, 'loading')
})

test('P6.8-B2 B detail failure still never paints A', () => {
  let surface = createDesktopConversationSurface<string, { id: string }>(WORKSPACE_A, CONVERSATION_A)
  surface = applyDesktopConversationDetail(surface, surface.detailRequestEpoch, CONVERSATION_A, {
    ok: true,
    messages: ['transcript A'],
  })
  surface = selectDesktopConversation(surface, WORKSPACE_A, CONVERSATION_B)
  const failed = applyDesktopConversationDetail(surface, surface.detailRequestEpoch, CONVERSATION_B, {
    ok: false,
    error: '会话不存在。',
  })
  assert.equal(failed.conversationId, CONVERSATION_B)
  assert.deepEqual(failed.messages, [])
  assert.equal(failed.messagesStatus, 'error')
  assert.equal(failed.messagesError, '会话不存在。')
})

test('P6.8-B3 first render after switch B hides A liveText without waiting for scope effect', () => {
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
    providerName: 'loopback',
    requestedModel: 'fake-model',
    sendEpoch: live.sendEpoch,
  })
  live = applyDesktopConversationEvent(live, {
    type: 'delta',
    invocationId: INVOCATION_A,
    workspaceId: WORKSPACE_A,
    conversationId: CONVERSATION_A,
    text: 'streaming A',
  })
  const firstRenderOnB = desktopInvocationLiveProjection(live, WORKSPACE_B, CONVERSATION_B)
  assert.equal(firstRenderOnB.visible, false)
  assert.equal(firstRenderOnB.liveText, '')
})

test('P6.8-B4 first render after switch B hides A liveMeta Provider and model', () => {
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
    providerName: 'loopback-A',
    requestedModel: 'model-A',
    sendEpoch: live.sendEpoch,
  })
  const firstRenderOnB = desktopInvocationLiveProjection(live, WORKSPACE_B, CONVERSATION_B)
  assert.equal(firstRenderOnB.visible, false)
  assert.equal(firstRenderOnB.liveMeta, null)
  const stillOnA = desktopInvocationLiveProjection(live, WORKSPACE_A, CONVERSATION_A)
  assert.equal(stillOnA.liveMeta?.providerName, 'loopback-A')
  assert.equal(stillOnA.liveMeta?.requestedModel, 'model-A')
})

test('P6.8-B5 same-scope later detail wins when an older request returns last', () => {
  let surface = selectDesktopConversation(
    createDesktopConversationSurface<string, { id: string }>(WORKSPACE_A, CONVERSATION_A),
    WORKSPACE_A,
    CONVERSATION_A,
  )
  const first = beginDesktopSurfaceDetailRequest(surface)
  const second = beginDesktopSurfaceDetailRequest(first.surface)
  const afterSecond = applyDesktopConversationDetail(second.surface, second.epoch, CONVERSATION_A, {
    ok: true,
    messages: ['R2'],
  })
  const afterFirst = applyDesktopConversationDetail(afterSecond, first.epoch, CONVERSATION_A, {
    ok: true,
    messages: ['R1'],
  })
  assert.deepEqual(afterFirst.messages, ['R2'])
})

test('P6.8-B6 A to B to A drops the first late A detail', () => {
  let surface = selectDesktopConversation(
    createDesktopConversationSurface<string, { id: string }>(WORKSPACE_A, CONVERSATION_A),
    WORKSPACE_A,
    CONVERSATION_A,
  )
  const firstA = beginDesktopSurfaceDetailRequest(surface)
  surface = selectDesktopConversation(firstA.surface, WORKSPACE_A, CONVERSATION_B)
  surface = selectDesktopConversation(surface, WORKSPACE_A, CONVERSATION_A)
  const secondA = surface.detailRequestEpoch
  const lateFirst = applyDesktopConversationDetail(surface, firstA.epoch, CONVERSATION_A, {
    ok: true,
    messages: ['old A'],
  })
  assert.deepEqual(lateFirst.messages, [])
  const current = applyDesktopConversationDetail(lateFirst, secondA, CONVERSATION_A, {
    ok: true,
    messages: ['new A'],
  })
  assert.deepEqual(current.messages, ['new A'])
})

test('P6.8-B7 archive A completing after switch B leaves the view on B', () => {
  let surface = selectDesktopConversation(
    createDesktopConversationSurface<string, { id: string }>(WORKSPACE_A, CONVERSATION_A),
    WORKSPACE_A,
    CONVERSATION_A,
  )
  const archive = beginDesktopSurfaceMutation(surface)
  surface = selectDesktopConversation(archive.surface, WORKSPACE_A, CONVERSATION_B)
  const afterArchive = applyDesktopConversationArchive(
    surface,
    archive,
    CONVERSATION_A,
    [
      { id: CONVERSATION_A },
      { id: CONVERSATION_B },
    ],
    CONVERSATION_A,
  )
  assert.equal(afterArchive.conversationId, CONVERSATION_B)
  assert.equal(afterArchive.conversations.some((item) => item.id === CONVERSATION_A), true)
})

test('P6.8-B8 send A completion after switch B does not change B', () => {
  let surface = selectDesktopConversation(
    createDesktopConversationSurface<string, { id: string }>(WORKSPACE_A, CONVERSATION_A),
    WORKSPACE_A,
    CONVERSATION_A,
  )
  const send = beginDesktopSurfaceDetailRequest(surface)
  surface = selectDesktopConversation(send.surface, WORKSPACE_A, CONVERSATION_B)
  surface = applyDesktopConversationDetail(surface, surface.detailRequestEpoch, CONVERSATION_B, {
    ok: true,
    messages: ['transcript B'],
  })
  const afterSend = applyDesktopConversationCompletion(surface, send.epoch, CONVERSATION_A, [
    'send A done',
  ])
  assert.equal(afterSend.conversationId, CONVERSATION_B)
  assert.deepEqual(afterSend.messages, ['transcript B'])
})

test('P6.8-B9 retry A completion after switch B does not change B', () => {
  let surface = selectDesktopConversation(
    createDesktopConversationSurface<string, { id: string }>(WORKSPACE_A, CONVERSATION_A),
    WORKSPACE_A,
    CONVERSATION_A,
  )
  const retry = beginDesktopSurfaceDetailRequest(surface)
  surface = selectDesktopConversation(retry.surface, WORKSPACE_A, CONVERSATION_B)
  surface = applyDesktopConversationDetail(surface, surface.detailRequestEpoch, CONVERSATION_B, {
    ok: true,
    messages: ['transcript B'],
  })
  const afterRetry = applyDesktopConversationCompletion(surface, retry.epoch, CONVERSATION_A, [
    'retry A done',
  ])
  assert.equal(afterRetry.conversationId, CONVERSATION_B)
  assert.deepEqual(afterRetry.messages, ['transcript B'])
})

test('P6.8-B10 unmount drops later UI projection', () => {
  let surface = selectDesktopConversation(
    createDesktopConversationSurface<string, { id: string }>(WORKSPACE_A, CONVERSATION_A),
    WORKSPACE_A,
    CONVERSATION_A,
  )
  const detail = beginDesktopSurfaceDetailRequest(surface)
  const load = beginDesktopSurfaceWorkspaceLoad(detail.surface)
  surface = unmountDesktopConversationSurface(load.surface)
  const afterDetail = applyDesktopConversationDetail(surface, detail.epoch, CONVERSATION_A, {
    ok: true,
    messages: ['should not paint'],
  })
  const afterLoad = applyDesktopWorkspaceLoad(
    afterDetail,
    load,
    [{ id: CONVERSATION_A }],
    CONVERSATION_A,
  )
  const afterComplete = applyDesktopConversationCompletion(afterLoad, detail.epoch, CONVERSATION_A, [
    'should not paint',
  ])
  assert.equal(afterDetail.mounted, false)
  assert.deepEqual(afterDetail.messages, [])
  assert.deepEqual(afterLoad.messages, [])
  assert.deepEqual(afterComplete.messages, [])
  assert.equal(afterComplete.messagesStatus, 'loading')
})

test('P6.8-B11 late create on A does not mutate workspace B list', () => {
  let surface = selectDesktopConversation(
    createDesktopConversationSurface<string, { id: string }>(WORKSPACE_A, CONVERSATION_A),
    WORKSPACE_A,
    CONVERSATION_A,
  )
  surface = {
    ...surface,
    conversations: [{ id: CONVERSATION_A }],
  }
  const create = beginDesktopSurfaceMutation(surface)
  surface = selectDesktopConversation(create.surface, WORKSPACE_B, CONVERSATION_B)
  assert.deepEqual(surface.conversations, [])
  const afterCreate = applyDesktopConversationCreate(surface, create, { id: `conversation_${'c'.repeat(32)}` })
  assert.equal(afterCreate.workspaceId, WORKSPACE_B)
  assert.equal(afterCreate.conversationId, CONVERSATION_B)
  assert.deepEqual(afterCreate.conversations, [])
})

test('P6.8-B12 late archive on workspace A does not mutate workspace B list or selection', () => {
  let surface = selectDesktopConversation(
    createDesktopConversationSurface<string, { id: string }>(WORKSPACE_A, CONVERSATION_A),
    WORKSPACE_A,
    CONVERSATION_A,
  )
  surface = {
    ...surface,
    conversations: [{ id: CONVERSATION_A }, { id: CONVERSATION_B }],
  }
  const archive = beginDesktopSurfaceMutation(surface)
  surface = selectDesktopConversation(archive.surface, WORKSPACE_B, CONVERSATION_B)
  const afterArchive = applyDesktopConversationArchive(
    surface,
    archive,
    CONVERSATION_A,
    [{ id: CONVERSATION_A }],
    CONVERSATION_A,
  )
  assert.equal(afterArchive.workspaceId, WORKSPACE_B)
  assert.equal(afterArchive.conversationId, CONVERSATION_B)
  assert.deepEqual(afterArchive.conversations, [])
})

test('P6.8-B same-workspace archive while viewing B updates the sidebar without jumping', () => {
  let surface = selectDesktopConversation(
    createDesktopConversationSurface<string, { id: string }>(WORKSPACE_A, CONVERSATION_A),
    WORKSPACE_A,
    CONVERSATION_A,
  )
  const archive = beginDesktopSurfaceMutation(surface)
  surface = selectDesktopConversation(archive.surface, WORKSPACE_A, CONVERSATION_B)
  const afterArchive = applyDesktopConversationArchive(
    surface,
    archive,
    CONVERSATION_A,
    [{ id: CONVERSATION_B }],
    CONVERSATION_A,
  )
  assert.equal(afterArchive.conversationId, CONVERSATION_B)
  assert.deepEqual(afterArchive.conversations, [{ id: CONVERSATION_B }])
})

test('P6.8-B overlapping create and archive on the current workspace both apply', () => {
  let surface = selectDesktopConversation(
    createDesktopConversationSurface<string, { id: string }>(WORKSPACE_A, CONVERSATION_A),
    WORKSPACE_A,
    CONVERSATION_A,
  )
  surface = { ...surface, conversations: [{ id: CONVERSATION_A }] }
  const create = beginDesktopSurfaceMutation(surface)
  const archive = beginDesktopSurfaceMutation(create.surface)
  const createdId = `conversation_${'c'.repeat(32)}`
  const afterCreate = applyDesktopConversationCreate(archive.surface, create, { id: createdId })
  assert.equal(afterCreate.conversations.some((item) => item.id === createdId), true)
  const afterArchive = applyDesktopConversationArchive(
    afterCreate,
    archive,
    CONVERSATION_A,
    afterCreate.conversations.filter((item) => item.id !== CONVERSATION_A),
    createdId,
  )
  assert.equal(afterArchive.conversations.some((item) => item.id === createdId), true)
  assert.equal(afterArchive.conversations.some((item) => item.id === CONVERSATION_A), false)
})

test('P6.8-B late workspace A load does not replace workspace B list', () => {
  let surface = selectDesktopConversation(
    createDesktopConversationSurface<string, { id: string }>(WORKSPACE_A, CONVERSATION_A),
    WORKSPACE_A,
    CONVERSATION_A,
  )
  const loadA = beginDesktopSurfaceWorkspaceLoad(surface)
  surface = selectDesktopConversation(loadA.surface, WORKSPACE_B, CONVERSATION_B)
  const loadB = beginDesktopSurfaceWorkspaceLoad(surface)
  const afterLateA = applyDesktopWorkspaceLoad(loadB.surface, loadA, [{ id: CONVERSATION_A }], CONVERSATION_A)
  assert.equal(afterLateA.workspaceId, WORKSPACE_B)
  assert.deepEqual(afterLateA.conversations, [])
  const afterB = applyDesktopWorkspaceLoad(afterLateA, loadB, [{ id: CONVERSATION_B }], CONVERSATION_B)
  assert.deepEqual(afterB.conversations, [{ id: CONVERSATION_B }])
})
