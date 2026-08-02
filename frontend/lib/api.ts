import axios, { type AxiosError, type AxiosInstance, type InternalAxiosRequestConfig } from 'axios'
import type {
  ApiErrorResponse,
  DocumentDownloadURL,
  DocumentList,
  DocumentRead,
  DocumentUploadResponse,
  HealthResponse,
  LoginRequest,
  PlaygroundResponse,
  RefreshResponse,
  RegisterRequest,
  SearchResponse,
  TableInfo,
  TokenResponse,
  UserPublic,
  WorkspaceList,
  WorkspaceMembershipList,
  WorkspaceRead,
  WorkspaceRunList,
  WorkspaceTemplateList,
} from './types'
import { classifyAuthFailure, invalidateAuthSession, redirectToLogin } from './auth-session'
import { getAccessToken, getRefreshToken, setTokens } from './tokens'

/**
 * Axios instance with JWT auth + auto-refresh on 401.
 *
 * - Base path: /api/v1 (Next.js proxies to the versioned backend API)
 * - Request interceptor: attach Authorization header if a token is present
 * - Response interceptor: on 401, try to refresh once; if refresh fails,
 *   clear tokens and redirect to /login
 */

// Singleton promise to prevent concurrent refresh attempts
let refreshPromise: Promise<string> | null = null

export const API_PREFIX = '/api/v1'

function apiUrl(path: string): string {
  return `${API_PREFIX}${path.startsWith('/') ? path : `/${path}`}`
}

export const api: AxiosInstance = axios.create({
  baseURL: API_PREFIX,
  timeout: 30_000,
  headers: {
    'Content-Type': 'application/json',
  },
})

// -----------------------------------------------------------
// Request interceptor: attach Bearer token
// -----------------------------------------------------------
api.interceptors.request.use((config: InternalAxiosRequestConfig) => {
  const token = getAccessToken()
  if (token) {
    config.headers.set('Authorization', `Bearer ${token}`)
  }
  return config
})

// -----------------------------------------------------------
// Response interceptor: auto-refresh on 401
// -----------------------------------------------------------
api.interceptors.response.use(
  (response) => response,
  async (error: AxiosError<ApiErrorResponse>) => {
    const originalRequest = error.config as InternalAxiosRequestConfig & {
      _retried?: boolean
    }

    // Skip refresh attempt for the refresh endpoint itself (avoid loop)
    const isRefreshCall = originalRequest?.url?.includes('/auth/refresh')
    // Skip if we've already retried this request
    const alreadyRetried = originalRequest?._retried === true

    if (error.response?.status === 401 && !isRefreshCall && !alreadyRetried) {
      originalRequest._retried = true
      try {
        const newAccessToken = await refreshAccessToken()
        originalRequest.headers.set('Authorization', `Bearer ${newAccessToken}`)
        return api(originalRequest)
      } catch (refreshError) {
        const status = axios.isAxiosError(refreshError) ? refreshError.response?.status : undefined
        if (classifyAuthFailure(status) === 'invalid' || !getRefreshToken()) {
          invalidateAuthSession()
          redirectToLogin()
        }
        return Promise.reject(refreshError)
      }
    }

    return Promise.reject(error)
  },
)

/**
 * Refresh the access token using the stored refresh token.
 * Deduplicates concurrent calls via a singleton promise.
 */
export async function refreshAccessToken(): Promise<string> {
  if (refreshPromise) return refreshPromise

  const refreshToken = getRefreshToken()
  if (!refreshToken) {
    invalidateAuthSession()
    throw new Error('No refresh token available')
  }

  refreshPromise = (async () => {
    try {
      // Use raw axios (not `api`) to bypass the interceptor loop
      const response = await axios.post<RefreshResponse>(apiUrl('/auth/refresh'), {
        refresh_token: refreshToken,
      })
      const { access_token, expires_in } = response.data
      // Compute new expires_at; refresh token stays as-is (no rotation in Phase 0)
      const expiresAt = Date.now() + expires_in * 1000
      setTokens(access_token, refreshToken, expiresAt)
      return access_token
    } catch (error) {
      const status = axios.isAxiosError(error) ? error.response?.status : undefined
      if (classifyAuthFailure(status) === 'invalid') {
        invalidateAuthSession()
      }
      throw error
    } finally {
      refreshPromise = null
    }
  })()

  return refreshPromise
}

// -----------------------------------------------------------
// Typed API surface (one function per endpoint)
// -----------------------------------------------------------
export const authApi = {
  register: (payload: RegisterRequest) =>
    api.post<TokenResponse>('/auth/register', payload).then((r) => r.data),

  login: (payload: LoginRequest) =>
    api.post<TokenResponse>('/auth/login', payload).then((r) => r.data),

  refresh: (refreshToken: string) =>
    api.post<RefreshResponse>('/auth/refresh', { refresh_token: refreshToken }).then((r) => r.data),

  me: () => api.get<UserPublic>('/auth/me').then((r) => r.data),
}

export const documentsApi = {
  list: (params?: { limit?: number; offset?: number; status?: string }) =>
    api.get<DocumentList>('/documents', { params }).then((r) => r.data),

  get: (id: string) => api.get<DocumentRead>(`/documents/${id}`).then((r) => r.data),

  upload: (file: File) => {
    const formData = new FormData()
    formData.append('file', file)
    return api
      .post<DocumentUploadResponse>('/documents', formData, {
        headers: { 'Content-Type': 'multipart/form-data' },
      })
      .then((r) => r.data)
  },

  downloadUrl: (id: string) =>
    api.get<DocumentDownloadURL>(`/documents/${id}/download`).then((r) => r.data),

  delete: (id: string) =>
    api.delete<{ id: string; deleted: boolean }>(`/documents/${id}`).then((r) => r.data),
}

export const databaseApi = {
  listTables: () => api.get<{ tables: TableInfo[] }>('/database/tables').then((r) => r.data),
}

export const healthApi = {
  liveness: () => axios.get<HealthResponse>('/health').then((r) => r.data),

  readiness: () => axios.get<HealthResponse>('/health/ready').then((r) => r.data),
}

export const ragApi = {
  search: (query: string, topK = 5) =>
    api.post<SearchResponse>('/rag/search', { query, top_k: topK }).then((r) => r.data),

  playground: (query: string, topK = 5, vectorTopK = 100, enableRerank = true) =>
    api
      .post<PlaygroundResponse>('/rag/playground', {
        query,
        top_k: topK,
        vector_top_k: vectorTopK,
        enable_rerank: enableRerank,
      })
      .then((r) => r.data),

  ask: (query: string, topK = 5) =>
    api.post<unknown>('/rag/ask', { query, top_k: topK, stream: true }, { responseType: 'stream' }),

  askStream: async (
    query: string,
    topK = 5,
    options: { signal?: AbortSignal } = {},
  ): Promise<Response> => {
    const requestStream = (accessToken: string | null): Promise<Response> => {
      const headers = new Headers({ 'Content-Type': 'application/json' })
      if (accessToken) {
        headers.set('Authorization', `Bearer ${accessToken}`)
      }

      return fetch(apiUrl('/rag/ask'), {
        method: 'POST',
        headers,
        body: JSON.stringify({ query, top_k: topK, stream: true }),
        signal: options.signal,
      })
    }

    const initialResponse = await requestStream(getAccessToken())
    if (initialResponse.status !== 401) return initialResponse

    const accessToken = await refreshAccessToken()
    options.signal?.throwIfAborted()
    return requestStream(accessToken)
  },
}

export const workspacesApi = {
  listTemplates: () =>
    api.get<WorkspaceTemplateList>('/workspace-templates').then((response) => response.data),

  list: () => api.get<WorkspaceList>('/workspaces').then((response) => response.data),

  get: (workspaceId: string) =>
    api.get<WorkspaceRead>(`/workspaces/${workspaceId}`).then((response) => response.data),

  create: (payload: { display_name: string; template_id: string; quota?: Record<string, number> }) =>
    api
      .post<WorkspaceRead>('/workspaces', payload, {
        headers: { 'Idempotency-Key': crypto.randomUUID() },
      })
      .then((response) => response.data),

  requestState: (
    workspaceId: string,
    state: 'start' | 'pause' | 'stop' | 'archive',
    expectedVersion: number,
  ) =>
    api
      .post<WorkspaceRead>(`/workspaces/${workspaceId}/${state}`, {
        expected_version: expectedVersion,
      })
      .then((response) => response.data),

  listMembers: (workspaceId: string) =>
    api
      .get<WorkspaceMembershipList>(`/workspaces/${workspaceId}/members`)
      .then((response) => response.data),

  listRuns: (workspaceId: string) =>
    api
      .get<WorkspaceRunList>(`/workspaces/${workspaceId}/runs`)
      .then((response) => response.data),

  createRun: async (workspaceId: string, generation: number, kind: 'batch' | 'interactive') => {
    const operationId = crypto.randomUUID()
    const requestDigest = await sha256Hex(
      JSON.stringify({ kind, expected_workspace_generation: generation }),
    )
    return api
      .post<WorkspaceRunList['items'][number]>(
        `/workspaces/${workspaceId}/runs`,
        {
          kind,
          expected_workspace_generation: generation,
          request_digest: requestDigest,
        },
        { headers: { 'Idempotency-Key': operationId } },
      )
      .then((response) => response.data)
  },
}

async function sha256Hex(value: string): Promise<string> {
  const bytes = new TextEncoder().encode(value)
  const digest = await crypto.subtle.digest('SHA-256', bytes)
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, '0')).join('')
}

/**
 * Extract a friendly error message from any thrown API error.
 */
export function getApiErrorMessage(error: unknown, fallback = 'Something went wrong'): string {
  if (axios.isAxiosError(error)) {
    const data = error.response?.data as ApiErrorResponse | undefined
    if (data?.error?.message) return data.error.message
    if (error.message) return error.message
  }
  if (error instanceof Error) return error.message
  return fallback
}
