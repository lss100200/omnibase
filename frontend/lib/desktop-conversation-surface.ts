/**
 * Conversation-surface projection fence (P6.8-B).
 *
 * Scope generation still isolates A↔B view identity. Request epochs isolate
 * out-of-order responses for the same conversation. A new detail/send/retry
 * or workspace/mutation request invalidates older promises even when the
 * visible scope has not changed.
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
  readonly mounted: boolean
}

export type DesktopConversationDetailResult<TMessage> =
  | Readonly<{ ok: true; messages: readonly TMessage[] }>
  | Readonly<{ ok: false; error: string }>

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
  return {
    ...surface,
    workspaceId,
    conversationId,
    messages: [] as readonly TMessage[],
    messagesStatus: conversationId === null ? 'empty' : 'loading',
    messagesError: null,
    detailRequestEpoch: surface.detailRequestEpoch + 1,
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
} {
  const epoch = surface.workspaceLoadEpoch + 1
  return { surface: { ...surface, workspaceLoadEpoch: epoch }, epoch }
}

export function beginDesktopSurfaceMutation<TMessage, TConversation extends { readonly id: string }>(
  surface: DesktopConversationSurface<TMessage, TConversation>,
): {
  readonly surface: DesktopConversationSurface<TMessage, TConversation>
  readonly epoch: number
} {
  const epoch = surface.mutationEpoch + 1
  return { surface: { ...surface, mutationEpoch: epoch }, epoch }
}

function rejectIfStale<TMessage, TConversation extends { readonly id: string }>(
  surface: DesktopConversationSurface<TMessage, TConversation>,
  startedEpoch: number,
  currentEpoch: number,
): boolean {
  return !surface.mounted || startedEpoch !== currentEpoch
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
  startedEpoch: number,
  conversations: readonly TConversation[],
  selectedConversationId: string | null,
): DesktopConversationSurface<TMessage, TConversation> {
  if (rejectIfStale(surface, startedEpoch, surface.workspaceLoadEpoch)) return surface
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

export function applyDesktopConversationArchive<TMessage, TConversation extends { readonly id: string }>(
  surface: DesktopConversationSurface<TMessage, TConversation>,
  startedEpoch: number,
  archivedId: string,
  conversations: readonly TConversation[],
  nextConversationId: string | null,
): DesktopConversationSurface<TMessage, TConversation> {
  if (!surface.mounted) return surface
  if (startedEpoch !== surface.mutationEpoch) {
    return { ...surface, conversations }
  }
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
