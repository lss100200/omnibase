import assert from 'node:assert/strict'
import test, { afterEach, beforeEach } from 'node:test'
import axios from 'axios'
import {
  API_PREFIX,
  agentBuilderApi,
  agentAlphaApi,
  api,
  authApi,
  databaseApi,
  documentsApi,
  healthApi,
  isPublicAuthRequest,
  ragApi,
  workspacesApi,
} from './api'
import { clearTokens, setTokens } from './tokens'

class MemoryStorage implements Storage {
  private readonly values = new Map<string, string>()

  get length(): number {
    return this.values.size
  }

  clear(): void {
    this.values.clear()
  }

  getItem(key: string): string | null {
    return this.values.get(key) ?? null
  }

  key(index: number): string | null {
    return [...this.values.keys()][index] ?? null
  }

  removeItem(key: string): void {
    this.values.delete(key)
  }

  setItem(key: string, value: string): void {
    this.values.set(key, value)
  }
}

const localStorage = new MemoryStorage()
const originalFetch = globalThis.fetch
const originalAxiosPost = axios.post
const originalAxiosGet = axios.get
const originalApiAdapter = api.defaults.adapter
let assignedLocation = ''

Object.defineProperty(globalThis, 'window', {
  configurable: true,
  value: {
    localStorage,
    location: {
      pathname: '/',
      search: '',
      hash: '',
      href: '',
      assign: (value: string) => {
        assignedLocation = value
      },
    },
  },
})

beforeEach(() => {
  clearTokens()
  localStorage.clear()
  assignedLocation = ''
})

afterEach(() => {
  globalThis.fetch = originalFetch
  axios.post = originalAxiosPost
  axios.get = originalAxiosGet
  api.defaults.adapter = originalApiAdapter
})

test('REST clients resolve resource paths under the versioned API prefix', async () => {
  const urls: string[] = []
  api.defaults.adapter = async (config) => {
    urls.push(axios.getUri(config))
    return {
      data: {},
      status: 200,
      statusText: 'OK',
      headers: {},
      config,
    }
  }

  await authApi.register({ email: 'user@example.com', password: 'password', tenant_name: 'Test' })
  await authApi.login({ email: 'user@example.com', password: 'password' })
  await authApi.refresh('refresh-token')
  await authApi.me()
  await documentsApi.list({ limit: 1, offset: 0 })
  await documentsApi.get('doc-1')
  await documentsApi.upload(new File(['content'], 'note.txt', { type: 'text/plain' }))
  await documentsApi.downloadUrl('doc-1')
  await documentsApi.delete('doc-1')
  await databaseApi.listTables()
  await ragApi.search('hello')
  await ragApi.playground('hello')
  await ragApi.ask('hello')

  assert.equal(API_PREFIX, '/api/v1')
  assert.deepEqual(urls, [
    '/api/v1/auth/register',
    '/api/v1/auth/login',
    '/api/v1/auth/refresh',
    '/api/v1/auth/me',
    '/api/v1/documents?limit=1&offset=0',
    '/api/v1/documents/doc-1',
    '/api/v1/documents',
    '/api/v1/documents/doc-1/download',
    '/api/v1/documents/doc-1',
    '/api/v1/database/tables',
    '/api/v1/rag/search',
    '/api/v1/rag/playground',
    '/api/v1/rag/ask',
  ])
})

test('public auth endpoint classification is exact and accepts prefixed URLs', () => {
  assert.equal(isPublicAuthRequest('/auth/login'), true)
  assert.equal(isPublicAuthRequest('/api/v1/auth/register'), true)
  assert.equal(isPublicAuthRequest('http://localhost/api/v1/auth/refresh?attempt=1'), true)
  assert.equal(isPublicAuthRequest('/auth/me'), false)
  assert.equal(isPublicAuthRequest('/auth/login-history'), false)
  assert.equal(isPublicAuthRequest(undefined), false)
})

test('anonymous login 401 preserves the backend error and never attempts refresh', async () => {
  let refreshCount = 0
  axios.post = (async () => {
    refreshCount += 1
    throw new Error('refresh must not run')
  }) as typeof axios.post
  api.defaults.adapter = async (config) => {
    throw new axios.AxiosError(
      'Request failed with status code 401',
      axios.AxiosError.ERR_BAD_REQUEST,
      config,
      undefined,
      {
        data: { error: { code: 'invalid_credentials', message: 'Invalid email or password' } },
        status: 401,
        statusText: 'Unauthorized',
        headers: {},
        config,
      },
    )
  }

  await assert.rejects(
    authApi.login({ email: 'user@example.com', password: 'wrong-password' }),
    (error: unknown) => {
      assert.equal(axios.isAxiosError(error), true)
      if (!axios.isAxiosError(error)) return false
      assert.equal(error.response?.status, 401)
      assert.deepEqual(error.response?.data, {
        error: { code: 'invalid_credentials', message: 'Invalid email or password' },
      })
      return true
    },
  )

  assert.equal(refreshCount, 0)
  assert.equal(assignedLocation, '')
})

test('public auth requests neither attach stale bearer tokens nor refresh stale sessions', async () => {
  setTokens('stale-access', 'stale-refresh', Date.now() + 60_000)
  let requestAuthorization: unknown = null
  let refreshCount = 0
  axios.post = (async () => {
    refreshCount += 1
    throw new Error('refresh must not run')
  }) as typeof axios.post
  api.defaults.adapter = async (config) => {
    requestAuthorization = config.headers.get('Authorization')
    throw new axios.AxiosError(
      'Request failed with status code 401',
      axios.AxiosError.ERR_BAD_REQUEST,
      config,
      undefined,
      {
        data: { error: { code: 'invalid_credentials', message: 'Invalid email or password' } },
        status: 401,
        statusText: 'Unauthorized',
        headers: {},
        config,
      },
    )
  }

  await assert.rejects(authApi.login({ email: 'user@example.com', password: 'wrong-password' }))

  assert.equal(requestAuthorization ?? null, null)
  assert.equal(refreshCount, 0)
  assert.equal(assignedLocation, '')
})

test('protected 401 without a refresh token preserves the original response', async () => {
  setTokens('stale-access', '', Date.now() + 60_000)
  api.defaults.adapter = async (config) => {
    throw new axios.AxiosError(
      'Request failed with status code 401',
      axios.AxiosError.ERR_BAD_REQUEST,
      config,
      undefined,
      {
        data: { error: { code: 'invalid_access_token', message: 'Access token is invalid' } },
        status: 401,
        statusText: 'Unauthorized',
        headers: {},
        config,
      },
    )
  }

  await assert.rejects(authApi.me(), (error: unknown) => {
    assert.equal(axios.isAxiosError(error), true)
    if (!axios.isAxiosError(error)) return false
    assert.equal(error.response?.data.error.message, 'Access token is invalid')
    assert.notEqual(error.message, 'No refresh token available')
    return true
  })

  assert.equal(assignedLocation, '/login?from=%2F')
})

test('health probes remain on unversioned root paths', async () => {
  const urls: string[] = []
  axios.get = (async (url: string) => {
    urls.push(url)
    return { data: { status: 'ok' } }
  }) as typeof axios.get

  await healthApi.liveness()
  await healthApi.readiness()

  assert.deepEqual(urls, ['/health', '/health/ready'])
})

test('database API exposes metadata browsing without arbitrary query execution', () => {
  assert.equal(typeof databaseApi.listTables, 'function')
  assert.equal('query' in databaseApi, false)
})

test('workspace browser client exposes control-plane routes without workspace-data writes', async () => {
  const requests: Array<{ url: string; method?: string; data?: string }> = []
  api.defaults.adapter = async (config) => {
    requests.push({ url: axios.getUri(config), method: config.method, data: config.data })
    return {
      data: config.url?.endsWith('/runs') ? { id: 'run-1' } : { items: [], total: 0 },
      status: 200,
      statusText: 'OK',
      headers: {},
      config,
    }
  }

  await workspacesApi.listTemplates()
  await workspacesApi.list()
  await workspacesApi.get('workspace-1')
  await workspacesApi.listMembers('workspace-1')
  await workspacesApi.listRuns('workspace-1')
  await workspacesApi.createRun('workspace-1', 3, 'interactive')

  assert.deepEqual(
    requests.map((request) => request.url),
    [
      '/api/v1/workspace-templates',
      '/api/v1/workspaces',
      '/api/v1/workspaces/workspace-1',
      '/api/v1/workspaces/workspace-1/members',
      '/api/v1/workspaces/workspace-1/runs',
      '/api/v1/workspaces/workspace-1/runs',
    ],
  )
  const createRun = requests.at(-1)
  assert.equal(createRun?.method, 'post')
  const payload = JSON.parse(createRun?.data ?? '{}') as Record<string, unknown>
  assert.match(String(payload.request_digest), /^[0-9a-f]{64}$/)
  assert.equal('workspace_data' in workspacesApi, false)
  assert.equal('promotion' in workspacesApi, false)
  assert.equal('snapshot' in workspacesApi, false)
})

test('agent builder creates only the closed tool-free profile', async () => {
  let requestUrl = ''
  let requestBody = ''
  let requestHeaders: unknown
  api.defaults.adapter = async (config) => {
    requestUrl = axios.getUri(config)
    requestBody = String(config.data ?? '')
    requestHeaders = config.headers
    return {
      data: { definition: {}, version: {}, installation: null },
      status: 201,
      statusText: 'Created',
      headers: {},
      config,
    }
  }
  await agentBuilderApi.create('workspace-1', {
    display_name: 'Evidence analyst',
    role_description: 'Review evidence.',
    instructions: 'Cite the supplied context.',
    assistant_tone: 'Concise.',
    provider_policy: 'user_default',
    knowledge_mode: 'workspace_read_only',
    max_context_tokens: 8192,
    max_output_tokens: 1024,
    max_wall_clock_seconds: 60,
    install_immediately: true,
  })
  assert.equal(requestUrl, '/api/v1/workspaces/workspace-1/agents')
  const body = JSON.parse(requestBody) as Record<string, unknown>
  assert.equal(body.provider_policy, 'user_default')
  assert.equal(body.knowledge_mode, 'workspace_read_only')
  assert.equal('allowed_tool_ids' in body, false)
  assert.match(JSON.stringify(requestHeaders), /Idempotency-Key/i)
})

test('askStream remains backward compatible when options are omitted', async () => {
  let url: string | URL | Request | undefined
  let request: RequestInit | undefined
  globalThis.fetch = async (input, init) => {
    url = input
    request = init
    return new Response(null, { status: 200 })
  }

  await ragApi.askStream('hello', 3)

  assert.equal(url, '/api/v1/rag/ask')
  assert.equal(request?.signal, undefined)
  assert.equal(request?.body, JSON.stringify({ query: 'hello', top_k: 3, stream: true }))
})

test('askStream passes the signal to the initial fetch', async () => {
  const controller = new AbortController()
  let requestSignal: AbortSignal | null | undefined
  globalThis.fetch = async (_input, init) => {
    requestSignal = init?.signal
    return new Response(null, { status: 200 })
  }

  await ragApi.askStream('hello', 5, { signal: controller.signal })

  assert.equal(requestSignal, controller.signal)
})

test('askStream passes the signal and refreshed token to a 401 retry', async () => {
  setTokens('old-access', 'refresh-token', Date.now() + 60_000)
  const controller = new AbortController()
  const requests: RequestInit[] = []
  const urls: Array<string | URL | Request> = []
  globalThis.fetch = async (input, init = {}) => {
    urls.push(input)
    requests.push(init)
    return new Response(null, { status: requests.length === 1 ? 401 : 200 })
  }
  let refreshUrl: string | undefined
  let refreshCount = 0
  axios.post = (async (url: string) => {
    refreshUrl = url
    refreshCount += 1
    return { data: { access_token: 'new-access', expires_in: 300 } }
  }) as typeof axios.post

  const response = await ragApi.askStream('hello', 5, { signal: controller.signal })

  assert.equal(response.status, 200)
  assert.deepEqual(urls, ['/api/v1/rag/ask', '/api/v1/rag/ask'])
  assert.equal(requests.length, 2)
  assert.equal(refreshUrl, '/api/v1/auth/refresh')
  assert.equal(refreshCount, 1)
  assert.equal(requests[0]?.signal, controller.signal)
  assert.equal(requests[1]?.signal, controller.signal)
  assert.equal(new Headers(requests[0]?.headers).get('Authorization'), 'Bearer old-access')
  assert.equal(new Headers(requests[1]?.headers).get('Authorization'), 'Bearer new-access')
})

test('askStream does not retry when aborted during token refresh', async () => {
  setTokens('old-access', 'refresh-token', Date.now() + 60_000)
  const controller = new AbortController()
  let fetchCount = 0
  globalThis.fetch = async () => {
    fetchCount += 1
    return new Response(null, { status: 401 })
  }

  let finishRefresh: (() => void) | undefined
  axios.post = (() =>
    new Promise<unknown>((resolve) => {
      finishRefresh = () => {
        resolve({ data: { access_token: 'new-access', expires_in: 300 } })
      }
    })) as typeof axios.post

  const request = ragApi.askStream('hello', 5, { signal: controller.signal })
  await new Promise<void>((resolve) => setTimeout(resolve, 0))
  controller.abort()
  assert.ok(finishRefresh)
  finishRefresh()

  await assert.rejects(request, (error: unknown) => {
    return error instanceof DOMException && error.name === 'AbortError'
  })
  assert.equal(fetchCount, 1)
})

test('Agent Alpha stream preserves idempotency and abort signal across a 401 retry', async () => {
  setTokens('old-access', 'refresh-token', Date.now() + 60_000)
  const controller = new AbortController()
  const requests: RequestInit[] = []
  const urls: Array<string | URL | Request> = []
  globalThis.fetch = async (input, init = {}) => {
    urls.push(input)
    requests.push(init)
    return new Response(null, { status: requests.length === 1 ? 401 : 200 })
  }
  axios.post = (async () => ({
    data: { access_token: 'new-access', expires_in: 300 },
  })) as typeof axios.post

  const response = await agentAlphaApi.invokeStream(
    'workspace-1',
    { agent_version_id: 'version-1', message: 'hello' },
    { signal: controller.signal, idempotencyKey: 'stable-alpha-key' },
  )

  assert.equal(response.status, 200)
  assert.deepEqual(urls, [
    '/api/v1/workspaces/workspace-1/agent-alpha/invoke',
    '/api/v1/workspaces/workspace-1/agent-alpha/invoke',
  ])
  assert.equal(requests[0]?.signal, controller.signal)
  assert.equal(requests[1]?.signal, controller.signal)
  assert.equal(new Headers(requests[0]?.headers).get('Idempotency-Key'), 'stable-alpha-key')
  assert.equal(new Headers(requests[1]?.headers).get('Idempotency-Key'), 'stable-alpha-key')
  assert.equal(new Headers(requests[0]?.headers).get('Authorization'), 'Bearer old-access')
  assert.equal(new Headers(requests[1]?.headers).get('Authorization'), 'Bearer new-access')
})

test('Agent Alpha 401 without a refresh token never exposes the internal refresh error', async () => {
  setTokens('stale-access', '', Date.now() + 60_000)
  let fetchCount = 0
  globalThis.fetch = async () => {
    fetchCount += 1
    return new Response(null, { status: 401 })
  }

  await assert.rejects(
    agentAlphaApi.invokeStream(
      'workspace-1',
      { agent_version_id: 'version-1', message: 'hello' },
      { idempotencyKey: 'stable-alpha-key' },
    ),
    (error: unknown) => error instanceof Error && error.message === 'auth_session_expired',
  )

  assert.equal(fetchCount, 1)
})

test('Agent Alpha cancel uses the versioned workspace invocation path', async () => {
  let requestUrl = ''
  api.defaults.adapter = async (config) => {
    requestUrl = axios.getUri(config)
    return {
      data: { invocation_id: 'invocation-1', cancellation_requested: true },
      status: 200,
      statusText: 'OK',
      headers: {},
      config,
    }
  }

  await agentAlphaApi.cancel('workspace-1', 'invocation-1')

  assert.equal(
    requestUrl,
    '/api/v1/workspaces/workspace-1/agent-alpha/invocations/invocation-1/cancel',
  )
})
