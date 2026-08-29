import type {
  DesktopWorkspaceFileAuthorization,
  DesktopWorkspaceFileEntry,
  DesktopWorkspaceFileList,
  DesktopWorkspaceFileRead,
} from './desktop-bridge'

export const P7_WORKSPACE_FILE_MAX_BYTES = 1_048_576
export const P7_WORKSPACE_DIRECTORY_MAX_ENTRIES = 500

const WORKSPACE_FILE_ERROR_MESSAGES: Readonly<Record<string, string>> = {
  desktop_native_response_invalid: '本机文件服务返回了无法验证的数据。',
  desktop_workspace_files_not_authorized: '目录授权已失效，请重新选择文件夹。',
  desktop_workspace_files_generation_conflict: '目录授权已更新，请重新选择文件夹。',
  desktop_workspace_files_generation_exhausted: '目录授权次数已达安全上限，请重新启动应用。',
  desktop_workspace_files_picker_cancelled: '已取消选择文件夹。',
  desktop_workspace_files_root_unsafe: '所选目录不符合本机安全边界。',
  desktop_workspace_files_path_invalid: '文件路径不符合工作区边界。',
  desktop_workspace_files_path_not_found: '文件或目录已不存在。',
  desktop_workspace_files_link_forbidden: '为避免越出授权目录，不能打开链接文件。',
  desktop_workspace_files_type_forbidden: '当前文件类型不支持只读预览。',
  desktop_workspace_files_sensitive_forbidden: '敏感文件不会显示在工作台中。',
  desktop_workspace_files_directory_too_large: '目录内容超过本次浏览上限。',
  desktop_workspace_files_file_too_large: '文件超过 1 MiB 只读预览上限。',
  desktop_workspace_files_not_utf8: '文件不是严格 UTF-8 文本，无法在代码视图中打开。',
  desktop_workspace_files_identity_drift: '文件在读取时发生变化，请重新打开。',
  desktop_workspace_files_unavailable: '本机文件服务当前不可用。',
}

export function p7WorkspaceFileErrorMessage(code: string | null): string | null {
  if (code === null) return null
  return WORKSPACE_FILE_ERROR_MESSAGES[code] ?? '本机文件操作未完成。'
}

export type P7WorkspaceFilesPhase = 'idle' | 'authorizing' | 'ready' | 'error'
export type P7WorkspaceFileReadPhase = 'idle' | 'loading' | 'ready' | 'error'

export interface P7WorkspaceFileDirectory {
  readonly directoryPath: string
  readonly status: 'idle' | 'loading' | 'ready' | 'error'
  readonly entries: readonly DesktopWorkspaceFileEntry[]
  readonly truncated: boolean
  readonly requestEpoch: number | null
  readonly errorCode: string | null
}

export interface P7WorkspaceFilesState {
  readonly workspaceId: string | null
  readonly scopeEpoch: number
  readonly nextRequestEpoch: number
  readonly authorizationRequestEpoch: number | null
  readonly phase: P7WorkspaceFilesPhase
  readonly authorization: DesktopWorkspaceFileAuthorization | null
  readonly directories: readonly P7WorkspaceFileDirectory[]
  readonly expandedDirectoryPaths: readonly string[]
  readonly selectedPath: string | null
  readonly readRequestEpoch: number | null
  readonly readPhase: P7WorkspaceFileReadPhase
  readonly openFile: DesktopWorkspaceFileRead | null
  readonly errorCode: string | null
}

export interface P7WorkspaceFileRequest {
  readonly kind: 'authorize' | 'list' | 'read'
  readonly workspaceId: string
  readonly authorizationGeneration: number | null
  readonly path: string
  readonly scopeEpoch: number
  readonly requestEpoch: number
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function hasExactKeys(value: Record<string, unknown>, expected: readonly string[]): boolean {
  const actual = Object.keys(value).sort()
  const sorted = [...expected].sort()
  return actual.length === sorted.length && actual.every((key, index) => key === sorted[index])
}

function isSafeNonNegativeInteger(value: unknown): value is number {
  return typeof value === 'number' && Number.isSafeInteger(value) && value >= 0
}

function isSafePositiveInteger(value: unknown): value is number {
  return typeof value === 'number' && Number.isSafeInteger(value) && value > 0
}

function isSafeName(value: unknown): value is string {
  return (
    typeof value === 'string' &&
    value.length > 0 &&
    value.length <= 255 &&
    value.normalize('NFKC') === value &&
    value.trim() === value &&
    !value.endsWith('.') &&
    !/[\\/:\u0000-\u001f\u007f\u202a-\u202e\u2066-\u2069]/u.test(value) &&
    !/%(?:2e|2f|3a|5c)/iu.test(value) &&
    !/^(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)/iu.test(value) &&
    !/^[A-Za-z]:/u.test(value) &&
    value !== '.' &&
    value !== '..'
  )
}

/** Empty string is the authorized root. Every other value is a logical path. */
export function isP7WorkspaceLogicalPath(value: unknown, allowRoot: boolean): value is string {
  if (typeof value !== 'string' || value.length > 4096) return false
  if (value === '') return allowRoot
  if (
    value.startsWith('/') ||
    value.startsWith('\\') ||
    value.endsWith('/') ||
    value.includes('//') ||
    value.includes('\\') ||
    /^[A-Za-z]:/u.test(value) ||
    /%(?:2e|2f|3a|5c)/iu.test(value)
  ) {
    return false
  }
  const parts = value.split('/')
  return parts.length <= 32 && parts.every((part) => isSafeName(part))
}

function childPath(directoryPath: string, name: string): string {
  return directoryPath === '' ? name : `${directoryPath}/${name}`
}

export function parseP7WorkspaceFileAuthorization(
  value: unknown,
  workspaceId: string,
): DesktopWorkspaceFileAuthorization | null {
  if (
    !isRecord(value) ||
    !hasExactKeys(value, ['authorizationGeneration', 'rootName', 'workspaceId']) ||
    value.workspaceId !== workspaceId ||
    !isSafeName(value.rootName) ||
    !isSafePositiveInteger(value.authorizationGeneration)
  ) {
    return null
  }
  return {
    workspaceId,
    rootName: value.rootName,
    authorizationGeneration: value.authorizationGeneration,
  }
}

function parseEntry(value: unknown, directoryPath: string): DesktopWorkspaceFileEntry | null {
  if (
    !isRecord(value) ||
    !hasExactKeys(value, ['kind', 'lastModifiedMs', 'name', 'path', 'sizeBytes']) ||
    !isSafeName(value.name) ||
    !isP7WorkspaceLogicalPath(value.path, false) ||
    value.path !== childPath(directoryPath, value.name) ||
    (value.kind !== 'file' && value.kind !== 'directory') ||
    !isSafeNonNegativeInteger(value.lastModifiedMs) ||
    (value.kind === 'file' ? !isSafeNonNegativeInteger(value.sizeBytes) : value.sizeBytes !== null)
  ) {
    return null
  }
  const sizeBytes =
    value.kind === 'file' && isSafeNonNegativeInteger(value.sizeBytes) ? value.sizeBytes : null
  return {
    path: value.path,
    name: value.name,
    kind: value.kind,
    sizeBytes,
    lastModifiedMs: value.lastModifiedMs,
  }
}

function parseDirectoryList(
  value: unknown,
  directoryPath: string,
): DesktopWorkspaceFileList | null {
  if (
    !isRecord(value) ||
    !hasExactKeys(value, ['directoryPath', 'entries', 'truncated']) ||
    value.directoryPath !== directoryPath ||
    !Array.isArray(value.entries) ||
    value.entries.length > P7_WORKSPACE_DIRECTORY_MAX_ENTRIES ||
    typeof value.truncated !== 'boolean'
  ) {
    return null
  }
  const entries: DesktopWorkspaceFileEntry[] = []
  const paths = new Set<string>()
  for (const candidate of value.entries) {
    const entry = parseEntry(candidate, directoryPath)
    if (entry === null || paths.has(entry.path)) return null
    paths.add(entry.path)
    entries.push(entry)
  }
  return { directoryPath, entries, truncated: value.truncated }
}

function parseFileRead(value: unknown, path: string): DesktopWorkspaceFileRead | null {
  if (
    !isRecord(value) ||
    !hasExactKeys(value, ['content', 'lastModifiedMs', 'path', 'sha256', 'sizeBytes']) ||
    value.path !== path ||
    typeof value.content !== 'string' ||
    !isSafeNonNegativeInteger(value.sizeBytes) ||
    value.sizeBytes > P7_WORKSPACE_FILE_MAX_BYTES ||
    !isSafeNonNegativeInteger(value.lastModifiedMs) ||
    typeof value.sha256 !== 'string' ||
    !/^[0-9a-f]{64}$/u.test(value.sha256) ||
    new TextEncoder().encode(value.content).byteLength !== value.sizeBytes
  ) {
    return null
  }
  return {
    path,
    content: value.content,
    sizeBytes: value.sizeBytes,
    lastModifiedMs: value.lastModifiedMs,
    sha256: value.sha256,
  }
}

function emptyState(workspaceId: string | null, scopeEpoch: number): P7WorkspaceFilesState {
  return {
    workspaceId,
    scopeEpoch,
    nextRequestEpoch: 0,
    authorizationRequestEpoch: null,
    phase: 'idle',
    authorization: null,
    directories: [],
    expandedDirectoryPaths: [],
    selectedPath: null,
    readRequestEpoch: null,
    readPhase: 'idle',
    openFile: null,
    errorCode: null,
  }
}

export function createP7WorkspaceFilesState(workspaceId: string | null): P7WorkspaceFilesState {
  return emptyState(workspaceId, 0)
}

export function switchP7WorkspaceFilesWorkspace(
  state: P7WorkspaceFilesState,
  workspaceId: string | null,
): P7WorkspaceFilesState {
  if (state.workspaceId === workspaceId) return state
  return emptyState(workspaceId, state.scopeEpoch + 1)
}

export function releaseP7WorkspaceFilesAuthorization(state: P7WorkspaceFilesState): {
  readonly state: P7WorkspaceFilesState
  readonly authorization: DesktopWorkspaceFileAuthorization | null
} {
  return {
    state: emptyState(state.workspaceId, state.scopeEpoch + 1),
    authorization: state.authorization,
  }
}

export function beginP7WorkspaceFileAuthorization(
  state: P7WorkspaceFilesState,
): { readonly state: P7WorkspaceFilesState; readonly request: P7WorkspaceFileRequest } | null {
  if (state.workspaceId === null) return null
  const scopeEpoch = state.scopeEpoch + 1
  const requestEpoch = state.nextRequestEpoch + 1
  return {
    state: {
      ...emptyState(state.workspaceId, scopeEpoch),
      nextRequestEpoch: requestEpoch,
      authorizationRequestEpoch: requestEpoch,
      phase: 'authorizing',
    },
    request: {
      kind: 'authorize',
      workspaceId: state.workspaceId,
      authorizationGeneration: null,
      path: '',
      scopeEpoch,
      requestEpoch,
    },
  }
}

function authorizationRequestIsCurrent(
  state: P7WorkspaceFilesState,
  request: P7WorkspaceFileRequest,
): boolean {
  return (
    request.kind === 'authorize' &&
    request.workspaceId === state.workspaceId &&
    request.scopeEpoch === state.scopeEpoch &&
    request.requestEpoch === state.authorizationRequestEpoch
  )
}

export function settleP7WorkspaceFileAuthorization(
  state: P7WorkspaceFilesState,
  request: P7WorkspaceFileRequest,
  value: unknown,
): P7WorkspaceFilesState {
  if (!authorizationRequestIsCurrent(state, request)) return state
  const authorization = parseP7WorkspaceFileAuthorization(value, request.workspaceId)
  if (authorization === null) {
    return {
      ...state,
      phase: 'error',
      authorizationRequestEpoch: null,
      errorCode: 'desktop_native_response_invalid',
    }
  }
  return {
    ...state,
    phase: 'ready',
    authorizationRequestEpoch: null,
    authorization,
    directories: [
      {
        directoryPath: '',
        status: 'idle',
        entries: [],
        truncated: false,
        requestEpoch: null,
        errorCode: null,
      },
    ],
    expandedDirectoryPaths: [''],
    errorCode: null,
  }
}

export function failP7WorkspaceFileAuthorization(
  state: P7WorkspaceFilesState,
  request: P7WorkspaceFileRequest,
  errorCode: string,
): P7WorkspaceFilesState {
  if (!authorizationRequestIsCurrent(state, request)) return state
  return {
    ...state,
    phase: errorCode === 'desktop_workspace_files_picker_cancelled' ? 'idle' : 'error',
    authorizationRequestEpoch: null,
    errorCode,
  }
}

function upsertDirectory(
  directories: readonly P7WorkspaceFileDirectory[],
  next: P7WorkspaceFileDirectory,
): readonly P7WorkspaceFileDirectory[] {
  const index = directories.findIndex((item) => item.directoryPath === next.directoryPath)
  if (index < 0) return [...directories, next]
  return directories.map((item, itemIndex) => (itemIndex === index ? next : item))
}

function authorizationWasInvalidated(errorCode: string): boolean {
  return (
    errorCode === 'desktop_native_input_invalid' ||
    errorCode === 'desktop_native_response_invalid' ||
    errorCode === 'desktop_workspace_files_not_authorized' ||
    errorCode === 'desktop_workspace_files_generation_conflict' ||
    errorCode === 'desktop_workspace_files_generation_exhausted' ||
    errorCode === 'desktop_workspace_files_identity_drift' ||
    errorCode === 'desktop_workspace_not_found' ||
    errorCode === 'desktop_workspace_archived'
  )
}

export function p7WorkspaceFileDirectory(
  state: P7WorkspaceFilesState,
  directoryPath: string,
): P7WorkspaceFileDirectory | null {
  return state.directories.find((item) => item.directoryPath === directoryPath) ?? null
}

function liveAuthorization(state: P7WorkspaceFilesState): DesktopWorkspaceFileAuthorization | null {
  return state.phase === 'ready' && state.authorization?.workspaceId === state.workspaceId
    ? state.authorization
    : null
}

export function p7WorkspaceFilesAuthorized(state: P7WorkspaceFilesState): boolean {
  return liveAuthorization(state) !== null
}

export function beginP7WorkspaceDirectoryList(
  state: P7WorkspaceFilesState,
  directoryPath: string,
): { readonly state: P7WorkspaceFilesState; readonly request: P7WorkspaceFileRequest } | null {
  const authorization = liveAuthorization(state)
  if (authorization === null || !isP7WorkspaceLogicalPath(directoryPath, true)) return null
  const requestEpoch = state.nextRequestEpoch + 1
  const previous = p7WorkspaceFileDirectory(state, directoryPath)
  const directory: P7WorkspaceFileDirectory = {
    directoryPath,
    status: 'loading',
    entries: previous?.entries ?? [],
    truncated: previous?.truncated ?? false,
    requestEpoch,
    errorCode: null,
  }
  return {
    state: {
      ...state,
      nextRequestEpoch: requestEpoch,
      directories: upsertDirectory(state.directories, directory),
    },
    request: {
      kind: 'list',
      workspaceId: authorization.workspaceId,
      authorizationGeneration: authorization.authorizationGeneration,
      path: directoryPath,
      scopeEpoch: state.scopeEpoch,
      requestEpoch,
    },
  }
}

function listRequestIsCurrent(
  state: P7WorkspaceFilesState,
  request: P7WorkspaceFileRequest,
): boolean {
  const authorization = liveAuthorization(state)
  const directory = p7WorkspaceFileDirectory(state, request.path)
  return (
    request.kind === 'list' &&
    authorization !== null &&
    request.workspaceId === state.workspaceId &&
    request.scopeEpoch === state.scopeEpoch &&
    request.authorizationGeneration === authorization.authorizationGeneration &&
    directory?.requestEpoch === request.requestEpoch
  )
}

export function settleP7WorkspaceDirectoryList(
  state: P7WorkspaceFilesState,
  request: P7WorkspaceFileRequest,
  value: unknown,
): P7WorkspaceFilesState {
  if (!listRequestIsCurrent(state, request)) return state
  const parsed = parseDirectoryList(value, request.path)
  if (parsed === null) {
    return {
      ...emptyState(state.workspaceId, state.scopeEpoch + 1),
      phase: 'error',
      errorCode: 'desktop_native_response_invalid',
    }
  }
  const previous = p7WorkspaceFileDirectory(state, request.path)!
  const directory: P7WorkspaceFileDirectory = {
    ...previous,
    status: 'ready',
    entries: parsed.entries,
    truncated: parsed.truncated,
    requestEpoch: null,
    errorCode: null,
  }
  return { ...state, directories: upsertDirectory(state.directories, directory) }
}

export function failP7WorkspaceDirectoryList(
  state: P7WorkspaceFilesState,
  request: P7WorkspaceFileRequest,
  errorCode: string,
): P7WorkspaceFilesState {
  if (!listRequestIsCurrent(state, request)) return state
  if (authorizationWasInvalidated(errorCode)) {
    return {
      ...emptyState(state.workspaceId, state.scopeEpoch + 1),
      phase: 'error',
      errorCode,
    }
  }
  const previous = p7WorkspaceFileDirectory(state, request.path)!
  return {
    ...state,
    directories: upsertDirectory(state.directories, {
      ...previous,
      status: 'error',
      requestEpoch: null,
      errorCode,
    }),
  }
}

export function setP7WorkspaceDirectoryExpanded(
  state: P7WorkspaceFilesState,
  directoryPath: string,
  expanded: boolean,
): P7WorkspaceFilesState {
  if (!isP7WorkspaceLogicalPath(directoryPath, true)) return state
  const paths = new Set(state.expandedDirectoryPaths)
  if (expanded) paths.add(directoryPath)
  else paths.delete(directoryPath)
  return { ...state, expandedDirectoryPaths: [...paths] }
}

export function beginP7WorkspaceFileRead(
  state: P7WorkspaceFilesState,
  path: string,
): { readonly state: P7WorkspaceFilesState; readonly request: P7WorkspaceFileRequest } | null {
  const authorization = liveAuthorization(state)
  if (authorization === null || !isP7WorkspaceLogicalPath(path, false)) return null
  const requestEpoch = state.nextRequestEpoch + 1
  return {
    state: {
      ...state,
      nextRequestEpoch: requestEpoch,
      selectedPath: path,
      readRequestEpoch: requestEpoch,
      readPhase: 'loading',
      openFile: null,
      errorCode: null,
    },
    request: {
      kind: 'read',
      workspaceId: authorization.workspaceId,
      authorizationGeneration: authorization.authorizationGeneration,
      path,
      scopeEpoch: state.scopeEpoch,
      requestEpoch,
    },
  }
}

function readRequestIsCurrent(
  state: P7WorkspaceFilesState,
  request: P7WorkspaceFileRequest,
): boolean {
  const authorization = liveAuthorization(state)
  return (
    request.kind === 'read' &&
    authorization !== null &&
    request.workspaceId === state.workspaceId &&
    request.scopeEpoch === state.scopeEpoch &&
    request.authorizationGeneration === authorization.authorizationGeneration &&
    request.requestEpoch === state.readRequestEpoch &&
    request.path === state.selectedPath
  )
}

export function settleP7WorkspaceFileRead(
  state: P7WorkspaceFilesState,
  request: P7WorkspaceFileRequest,
  value: unknown,
): P7WorkspaceFilesState {
  if (!readRequestIsCurrent(state, request)) return state
  const file = parseFileRead(value, request.path)
  if (file === null) {
    return {
      ...emptyState(state.workspaceId, state.scopeEpoch + 1),
      phase: 'error',
      errorCode: 'desktop_native_response_invalid',
    }
  }
  return {
    ...state,
    readRequestEpoch: null,
    readPhase: 'ready',
    openFile: file,
    errorCode: null,
  }
}

export function failP7WorkspaceFileRead(
  state: P7WorkspaceFilesState,
  request: P7WorkspaceFileRequest,
  errorCode: string,
): P7WorkspaceFilesState {
  if (!readRequestIsCurrent(state, request)) return state
  if (authorizationWasInvalidated(errorCode)) {
    return {
      ...emptyState(state.workspaceId, state.scopeEpoch + 1),
      phase: 'error',
      errorCode,
    }
  }
  return {
    ...state,
    readRequestEpoch: null,
    readPhase: 'error',
    openFile: null,
    errorCode,
  }
}
