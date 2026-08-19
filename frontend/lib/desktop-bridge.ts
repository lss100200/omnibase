'use client'

export interface DesktopOwner {
  readonly id: string
  readonly displayName: string
  readonly createdAt: string
  readonly updatedAt: string
}

export interface DesktopOwnerStatus {
  readonly initialized: boolean
  readonly owner: DesktopOwner | null
}

export interface DesktopOwnerBootstrapResult extends DesktopOwnerStatus {
  readonly initialized: true
  readonly created: boolean
  readonly owner: DesktopOwner
}

export type DesktopWorkspaceState = 'active' | 'archived'

export interface DesktopWorkspace {
  readonly id: string
  readonly ownerId: string
  readonly name: string
  readonly state: DesktopWorkspaceState
  readonly rowVersion: number
  readonly createdAt: string
  readonly updatedAt: string
}

export interface DesktopWorkspaceList {
  readonly items: readonly DesktopWorkspace[]
}

export interface DesktopWorkspaceMutationResult {
  readonly workspace: DesktopWorkspace
}

export type DesktopOperationResult<T> =
  | Readonly<{ ok: true; value: T }>
  | Readonly<{ ok: false; error: Readonly<{ code: string }> }>

export interface OmniBaseDesktopBridge {
  readonly app: {
    readonly getVersion: () => Promise<string>
  }
  readonly runtime: {
    readonly getStatus: () => Promise<{
      readonly phase: 'stopped' | 'starting' | 'ready' | 'failed'
      readonly attempts: number
      readonly lastError: string | null
    }>
    readonly retryStartup: () => Promise<{
      readonly phase: 'stopped' | 'starting' | 'ready' | 'failed'
      readonly attempts: number
      readonly lastError: string | null
    }>
  }
  readonly owner: {
    readonly getStatus: () => Promise<DesktopOperationResult<DesktopOwnerStatus>>
    readonly bootstrap: (input: {
      readonly displayName: string
    }) => Promise<DesktopOperationResult<DesktopOwnerBootstrapResult>>
  }
  readonly workspaces: {
    readonly list: () => Promise<DesktopOperationResult<DesktopWorkspaceList>>
    readonly create: (input: {
      readonly name: string
    }) => Promise<DesktopOperationResult<DesktopWorkspaceMutationResult>>
    readonly archive: (input: {
      readonly workspaceId: string
      readonly expectedRowVersion: number
    }) => Promise<DesktopOperationResult<DesktopWorkspaceMutationResult>>
  }
}

declare global {
  interface Window {
    readonly omnibaseDesktop?: OmniBaseDesktopBridge
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function hasFunction(value: Record<string, unknown>, name: string): boolean {
  return typeof value[name] === 'function'
}

export function resolveDesktopBridge(value: unknown): OmniBaseDesktopBridge | null {
  if (
    !isRecord(value) ||
    !isRecord(value.app) ||
    !hasFunction(value.app, 'getVersion') ||
    !isRecord(value.runtime) ||
    !hasFunction(value.runtime, 'getStatus') ||
    !hasFunction(value.runtime, 'retryStartup') ||
    !isRecord(value.owner) ||
    !hasFunction(value.owner, 'getStatus') ||
    !hasFunction(value.owner, 'bootstrap') ||
    !isRecord(value.workspaces) ||
    !hasFunction(value.workspaces, 'list') ||
    !hasFunction(value.workspaces, 'create') ||
    !hasFunction(value.workspaces, 'archive')
  ) {
    return null
  }
  return value as unknown as OmniBaseDesktopBridge
}

export function getDesktopBridge(): OmniBaseDesktopBridge | null {
  return typeof window === 'undefined' ? null : resolveDesktopBridge(window.omnibaseDesktop)
}
