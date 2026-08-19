import { DESKTOP_CHALLENGE_HEADER, proxyRequest } from './proxy'

const TOKEN_PATTERN = /^[a-f0-9]{64}$/
const CHALLENGE_PATTERN = /^[a-f0-9]{64}$/
const TOKEN_ENVIRONMENT_NAME = 'OMNIBASE_DESKTOP_INSTANCE_TOKEN'
const DESKTOP_ALLOWED_PROXY_ROUTES = new Set(['GET /health/ready'])

type RuntimeEnvironment = Readonly<Record<string, string | undefined>>

function stableError(status: number, code: string): Response {
  return new Response(JSON.stringify({ error: { code } }), {
    status,
    headers: {
      'Cache-Control': 'no-store',
      'Content-Type': 'application/json',
    },
  })
}

export function loadDesktopInstanceToken(environment: RuntimeEnvironment): string | null {
  const token = environment[TOKEN_ENVIRONMENT_NAME]
  if (token === undefined) return null
  if (!TOKEN_PATTERN.test(token)) {
    throw new Error('desktop_runtime_configuration_invalid')
  }
  return token
}

function configuredToken(environment: RuntimeEnvironment): string | Response | null {
  try {
    return loadDesktopInstanceToken(environment)
  } catch {
    return stableError(503, 'desktop_runtime_configuration_invalid')
  }
}

function isSafeDesktopTarget(target: string): boolean {
  try {
    const parsed = new URL(target)
    const port = Number(parsed.port)
    return (
      parsed.protocol === 'http:' &&
      parsed.hostname === '127.0.0.1' &&
      parsed.port !== '' &&
      Number.isInteger(port) &&
      port >= 1 &&
      port <= 65_535 &&
      parsed.username === '' &&
      parsed.password === '' &&
      parsed.pathname === '/' &&
      parsed.search === '' &&
      parsed.hash === ''
    )
  } catch {
    return false
  }
}

function desktopRouteAllowed(request: Request): boolean {
  const url = new URL(request.url)
  return url.search === '' && DESKTOP_ALLOWED_PROXY_ROUTES.has(`${request.method} ${url.pathname}`)
}

export async function proxyRuntimeRequest(
  target: string,
  request: Request,
  environment: RuntimeEnvironment,
): Promise<Response> {
  const token = configuredToken(environment)
  if (token instanceof Response) return token
  if (token === null) return proxyRequest(target, request)
  if (!isSafeDesktopTarget(target)) {
    return stableError(503, 'desktop_runtime_configuration_invalid')
  }
  if (!desktopRouteAllowed(request)) {
    return stableError(404, 'desktop_route_not_supported')
  }
  return proxyRequest(target, request, {
    desktopInstanceToken: token,
    dropBrowserCredentials: true,
  })
}

export async function proxyRuntimeHealthRequest(
  target: string,
  request: Request,
  environment: RuntimeEnvironment,
): Promise<Response> {
  const token = configuredToken(environment)
  if (token instanceof Response) return token
  if (token !== null) {
    if (!isSafeDesktopTarget(target)) {
      return stableError(503, 'desktop_runtime_configuration_invalid')
    }
    const url = new URL(request.url)
    if (request.method !== 'GET' || url.pathname !== '/health' || url.search !== '') {
      return stableError(404, 'desktop_route_not_supported')
    }
  }

  const challenge = request.headers.get(DESKTOP_CHALLENGE_HEADER)
  if (challenge !== null && !CHALLENGE_PATTERN.test(challenge)) {
    return stableError(400, 'desktop_runtime_challenge_invalid')
  }

  const upstream = await proxyRequest(target, request, {
    desktopInstanceToken: token,
    desktopChallenge: challenge,
    forwardDesktopProof: challenge !== null,
    dropBrowserCredentials: token !== null,
  })
  return upstream
}
