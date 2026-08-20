/**
 * Conversation-surface projection fence (P6.8-B).
 *
 * Scope generation still isolates A↔B view identity. Request epochs isolate
 * out-of-order responses for the same conversation. List writes (create,
 * archive, workspace load) are gated on workspace identity plus a
 * workspace-bound list generation so a late reply cannot paint another
 * workspace's sidebar. Per-request mutation tokens do not invalidate each
 * other while they still target the current workspace.
 */

export type DesktopConversationMessagesStatus = 'empty' | 'loading' | 'ready' | 'error'

export interface DesktopConversationSurface<TMessage = unknown, TConversation extends { readonly id: string } = { readonly id: string }> {
  readonly workspaceId: string | null
  readonly conversationId: string | null
  readonly messages: readonly TMessage[]
  readonly messagesStatus: DesktopConversationMessagesStatus
  readonly messagesError: string | null
  readonly conversations: readonly TConversation[]
  readonly detailRequestEpoch: number
  readonly workspaceLoadEpoch: number
  readonly mutationEpoch: number
  readonly listGeneration: number
  readonly mounted: boolean
}

export type DesktopConversationDetailResult<TMessage> =
  | Readonly<{ ok: true; messages: readonly TMessage[] }>
  | Readonly<{ ok: false; error: string }>

export interface DesktopSurfaceMutationStart {
  readonly epoch: number
  readonly workspaceId: string | null
  readonly listGeneration: number
}

export interface DesktopSurfaceWorkspaceLoadStart {
  readonly epoch: number
  readonly workspaceId: string | null
}

export function createDesktopConversationSurface<TMessage, TConversation extends { readonly id: string }>(
  workspaceId: string | null,
  conversationId: string | null,
): DesktopConversationSurface<TMessage, TConversation> {
  return {
    workspaceId,
    conversationId,
    messages: [] as readonly TMessage[],
    messagesStatus: conversationId === null ? 'empty' : 'loading',
    messagesError: null,
    conversations: [],
    detailRequestEpoch: 0,
    workspaceLoadEpoch: 0,
    mutationEpoch: 0,
    listGeneration: 0,
    mounted: true,
  }
}

export function unmountDesktopConversationSurface<TMessage, TConversation extends { readonly id: string }>(
  surface: DesktopConversationSurface<TMessage, TConversation>,
): DesktopConversationSurface<TMessage, TConversation> {
  return { ...surface, mounted: false }
}

export function selectDesktopConversation<TMessage, TConversation extends { readonly id: string }>(
  surface: DesktopConversationSurface<TMessage, TConversation>,
  workspaceId: string | null,
  conversationId: string | null,
): DesktopConversationSurface<TMessage, TConversation> {
  const workspaceChanged = surface.workspaceId !== workspaceId
  return {
    ...surface,
    workspaceId,
    conversationId,
    messages: [] as readonly TMessage[],
    messagesStatus: conversationId === null ? 'empty' : 'loading',
    messagesError: null,
    conversations: workspaceChanged ? [] : surface.conversations,
    detailRequestEpoch: surface.detailRequestEpoch + 1,
    listGeneration: workspaceChanged ? surface.listGeneration + 1 : surface.listGeneration,
    workspaceLoadEpoch: workspaceChanged ? surface.workspaceLoadEpoch + 1 : surface.workspaceLoadEpoch,
  }
}

export function beginDesktopSurfaceDetailRequest<TMessage, TConversation extends { readonly id: string }>(
  surface: DesktopConversationSurface<TMessage, TConversation>,
): {
  readonly surface: DesktopConversationSurface<TMessage, TConversation>
  readonly epoch: number
} {
  const epoch = surface.detailRequestEpoch + 1
  return { surface: { ...surface, detailRequestEpoch: epoch }, epoch }
}

export function beginDesktopSurfaceWorkspaceLoad<TMessage, TConversation extends { readonly id: string }>(
  surface: DesktopConversationSurface<TMessage, TConversation>,
): {
  readonly surface: DesktopConversationSurface<TMessage, TConversation>
  readonly epoch: number
  readonly workspaceId: string | null
} {
  const epoch = surface.workspaceLoadEpoch + 1
  return {
    surface: { ...surface, workspaceLoadEpoch: epoch },
    epoch,
    workspaceId: surface.workspaceId,
  }
}

export function beginDesktopSurfaceMutation<TMessage, TConversation extends { readonly id: string }>(
  surface: DesktopConversationSurface<TMessage, TConversation>,
): {
  readonly surface: DesktopConversationSurface<TMessage, TConversation>
  readonly epoch: number
  readonly workspaceId: string | null
  readonly listGeneration: number
} {
  const epoch = surface.mutationEpoch + 1
  return {
    surface: { ...surface, mutationEpoch: epoch },
    epoch,
    workspaceId: surface.workspaceId,
    listGeneration: surface.listGeneration,
  }
}

function rejectIfStale<TMessage, TConversation extends { readonly id: string }>(
  surface: DesktopConversationSurface<TMessage, TConversation>,
  startedEpoch: number,
  currentEpoch: number,
): boolean {
  return !surface.mounted || startedEpoch !== currentEpoch
}

function listMutationIsCurrent<TMessage, TConversation extends { readonly id: string }>(
  surface: DesktopConversationSurface<TMessage, TConversation>,
  started: Pick<DesktopSurfaceMutationStart, 'workspaceId' | 'listGeneration'>,
): boolean {
  return (
    surface.mounted &&
    surface.workspaceId === started.workspaceId &&
    surface.listGeneration === started.listGeneration
  )
}

export function applyDesktopConversationDetail<TMessage, TConversation extends { readonly id: string }>(
  surface: DesktopConversationSurface<TMessage, TConversation>,
  startedEpoch: number,
  conversationId: string,
  result: DesktopConversationDetailResult<TMessage>,
): DesktopConversationSurface<TMessage, TConversation> {
  if (rejectIfStale(surface, startedEpoch, surface.detailRequestEpoch)) return surface
  if (surface.conversationId !== conversationId) return surface
  if (!result.ok) {
    return {
      ...surface,
      messages: [] as readonly TMessage[],
      messagesStatus: 'error',
      messagesError: result.error,
    }
  }
  return {
    ...surface,
    messages: result.messages,
    messagesStatus: 'ready',
    messagesError: null,
  }
}

export function applyDesktopWorkspaceLoad<TMessage, TConversation extends { readonly id: string }>(
  surface: DesktopConversationSurface<TMessage, TConversation>,
  started: DesktopSurfaceWorkspaceLoadStart,
  conversations: readonly TConversation[],
  selectedConversationId: string | null,
): DesktopConversationSurface<TMessage, TConversation> {
  if (rejectIfStale(surface, started.epoch, surface.workspaceLoadEpoch)) return surface
  if (surface.workspaceId !== started.workspaceId) return surface
  if (surface.conversationId !== null) {
    return { ...surface, conversations }
  }
  return {
    ...surface,
    conversations,
    conversationId: selectedConversationId,
    messages: [] as readonly TMessage[],
    messagesStatus: selectedConversationId === null ? 'empty' : 'loading',
    messagesError: null,
  }
}

export function applyDesktopConversationCreate<TMessage, TConversation extends { readonly id: string }>(
  surface: DesktopConversationSurface<TMessage, TConversation>,
  started: DesktopSurfaceMutationStart,
  conversation: TConversation,
): DesktopConversationSurface<TMessage, TConversation> {
  if (!listMutationIsCurrent(surface, started)) return surface
  if (surface.conversations.some((item) => item.id === conversation.id)) {
    return {
      ...surface,
      conversations: surface.conversations.map((item) =>
        item.id === conversation.id ? conversation : item,
      ),
    }
  }
  return {
    ...surface,
    conversations: [conversation, ...surface.conversations],
  }
}

export function applyDesktopConversationArchive<TMessage, TConversation extends { readonly id: string }>(
  surface: DesktopConversationSurface<TMessage, TConversation>,
  started: DesktopSurfaceMutationStart,
  archivedId: string,
  conversations: readonly TConversation[],
  nextConversationId: string | null,
): DesktopConversationSurface<TMessage, TConversation> {
  if (!listMutationIsCurrent(surface, started)) return surface
  if (surface.conversationId !== archivedId) {
    return { ...surface, conversations }
  }
  return {
    ...surface,
    conversations,
    conversationId: nextConversationId,
    messages: [] as readonly TMessage[],
    messagesStatus: nextConversationId === null ? 'empty' : 'loading',
    messagesError: null,
    detailRequestEpoch: surface.detailRequestEpoch + 1,
  }
}

export function applyDesktopConversationCompletion<TMessage, TConversation extends { readonly id: string }>(
  surface: DesktopConversationSurface<TMessage, TConversation>,
  startedEpoch: number,
  conversationId: string,
  messages: readonly TMessage[],
  conversations?: readonly TConversation[],
): DesktopConversationSurface<TMessage, TConversation> {
  if (rejectIfStale(surface, startedEpoch, surface.detailRequestEpoch)) return surface
  if (surface.conversationId !== conversationId) return surface
  return {
    ...surface,
    messages,
    messagesStatus: 'ready',
    messagesError: null,
    conversations: conversations ?? surface.conversations,
  }
}

export function applyDesktopSurfaceError<TMessage, TConversation extends { readonly id: string }>(
  surface: DesktopConversationSurface<TMessage, TConversation>,
  startedEpoch: number,
  kind: 'detail' | 'workspace' | 'mutation',
): DesktopConversationSurface<TMessage, TConversation> | null {
  if (!surface.mounted) return null
  const current =
    kind === 'detail'
      ? surface.detailRequestEpoch
      : kind === 'workspace'
        ? surface.workspaceLoadEpoch
        : surface.mutationEpoch
  if (startedEpoch !== current) return null
  return surface
}
