export interface DesktopSurfaceScope {
  readonly workspaceId: string | null
  readonly conversationId: string | null
  readonly generation: number
}

export function createDesktopSurfaceScope(
  workspaceId: string | null,
  conversationId: string | null,
): DesktopSurfaceScope {
  return { workspaceId, conversationId, generation: 0 }
}

export function advanceDesktopSurfaceScope(
  current: DesktopSurfaceScope,
  workspaceId: string | null,
  conversationId: string | null,
): DesktopSurfaceScope {
  if (current.workspaceId === workspaceId && current.conversationId === conversationId) {
    return current
  }
  return {
    workspaceId,
    conversationId,
    generation: current.generation + 1,
  }
}

export function desktopSurfaceProjectionIsCurrent(
  started: DesktopSurfaceScope,
  current: DesktopSurfaceScope,
): boolean {
  return (
    started.generation === current.generation &&
    started.workspaceId === current.workspaceId &&
    started.conversationId === current.conversationId
  )
}

export function applyDesktopScopedProjection<T>(
  started: DesktopSurfaceScope,
  current: DesktopSurfaceScope,
  currentValue: T,
  incoming: T,
): T {
  return desktopSurfaceProjectionIsCurrent(started, current) ? incoming : currentValue
}
