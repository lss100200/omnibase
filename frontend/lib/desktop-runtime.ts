import { DESKTOP_CHALLENGE_HEADER, proxyRequest } from './proxy'

const TOKEN_PATTERN = /^[a-f0-9]{64}$/
const CHALLENGE_PATTERN = /^[a-f0-9]{64}$/
const TOKEN_ENVIRONMENT_NAME = 'OMNIBASE_DESKTOP_INSTANCE_TOKEN'

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

export async function proxyRuntimeRequest(
  target: string,
  request: Request,
  environment: RuntimeEnvironment,
): Promise<Response> {
  const token = configuredToken(environment)
  if (token instanceof Response) return token
  return proxyRequest(target, request, { desktopInstanceToken: token })
}

export async function proxyRuntimeHealthRequest(
  target: string,
  request: Request,
  environment: RuntimeEnvironment,
): Promise<Response> {
  const token = configuredToken(environment)
  if (token instanceof Response) return token

  const challenge = request.headers.get(DESKTOP_CHALLENGE_HEADER)
  if (challenge !== null && !CHALLENGE_PATTERN.test(challenge)) {
    return stableError(400, 'desktop_runtime_challenge_invalid')
  }

  const upstream = await proxyRequest(target, request, {
    desktopInstanceToken: token,
    desktopChallenge: challenge,
    forwardDesktopProof: challenge !== null,
  })
  return upstream
}
