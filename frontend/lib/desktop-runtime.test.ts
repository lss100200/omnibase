import assert from 'node:assert/strict'
import http from 'node:http'
import type { AddressInfo } from 'node:net'
import { test } from 'node:test'

import {
  loadDesktopInstanceToken,
  proxyRuntimeHealthRequest,
  proxyRuntimeRequest,
} from './desktop-runtime'
import { DESKTOP_CHALLENGE_HEADER, DESKTOP_INSTANCE_HEADER, DESKTOP_PROOF_HEADER } from './proxy'

const TOKEN = '0123456789abcdef'.repeat(4)
const CHALLENGE = 'abcdef0123456789'.repeat(4)
const BACKEND_PROOF = '1234567890abcdef'.repeat(4)
const ENVIRONMENT = { OMNIBASE_DESKTOP_INSTANCE_TOKEN: TOKEN }

function startUpstream(
  handler: (request: http.IncomingMessage, response: http.ServerResponse) => void,
): Promise<{ url: string; close: () => Promise<void> }> {
  return new Promise((resolve) => {
    const server = http.createServer(handler)
    server.listen(0, '127.0.0.1', () => {
      const { port } = server.address() as AddressInfo
      resolve({
        url: `http://127.0.0.1:${port}`,
        close: () => new Promise((done) => server.close(() => done())),
      })
    })
  })
}

test('desktop instance token is optional outside desktop mode and strict when present', () => {
  assert.equal(loadDesktopInstanceToken({}), null)
  assert.equal(loadDesktopInstanceToken(ENVIRONMENT), TOKEN)
  assert.throws(
    () => loadDesktopInstanceToken({ OMNIBASE_DESKTOP_INSTANCE_TOKEN: 'A'.repeat(64) }),
    /desktop_runtime_configuration_invalid/,
  )
  assert.throws(
    () => loadDesktopInstanceToken({ OMNIBASE_DESKTOP_INSTANCE_TOKEN: 'short' }),
    /desktop_runtime_configuration_invalid/,
  )
})

test('health proxy authenticates upstream and forwards only its native proof', async () => {
  let seenInstance = ''
  let seenChallenge = ''
  const upstream = await startUpstream((request, response) => {
    seenInstance = String(request.headers[DESKTOP_INSTANCE_HEADER] ?? '')
    seenChallenge = String(request.headers[DESKTOP_CHALLENGE_HEADER] ?? '')
    response.writeHead(200, {
      'Content-Type': 'application/json',
      [DESKTOP_PROOF_HEADER]: BACKEND_PROOF,
    })
    response.end('{"status":"ok"}')
  })
  try {
    const response = await proxyRuntimeHealthRequest(
      upstream.url,
      new Request('http://frontend.test/health', {
        headers: { [DESKTOP_CHALLENGE_HEADER]: CHALLENGE },
      }),
      ENVIRONMENT,
    )
    assert.equal(response.status, 200)
    assert.equal(seenInstance, TOKEN)
    assert.equal(seenChallenge, CHALLENGE)
    assert.equal(response.headers.get(DESKTOP_PROOF_HEADER), BACKEND_PROOF)
    assert.equal(response.headers.get('cache-control'), 'no-store')
    assert.deepEqual(await response.json(), { status: 'ok' })
  } finally {
    await upstream.close()
  }
})

test('health proxy cannot turn an arbitrary backend success into a native proof', async () => {
  const upstream = await startUpstream((_request, response) => {
    response.writeHead(200, { 'Content-Type': 'application/json' })
    response.end('{"status":"fake"}')
  })
  try {
    const response = await proxyRuntimeHealthRequest(
      upstream.url,
      new Request('http://frontend.test/health', {
        headers: { [DESKTOP_CHALLENGE_HEADER]: CHALLENGE },
      }),
      ENVIRONMENT,
    )
    assert.equal(response.status, 502)
    assert.equal(response.headers.get(DESKTOP_PROOF_HEADER), null)
    assert.deepEqual(await response.json(), {
      error: { code: 'upstream_unavailable' },
    })
  } finally {
    await upstream.close()
  }
})

test('health proxy never signs an upstream failure', async () => {
  const upstream = await startUpstream((_request, response) => {
    response.writeHead(503, { [DESKTOP_PROOF_HEADER]: BACKEND_PROOF })
    response.end('unavailable')
  })
  try {
    const response = await proxyRuntimeHealthRequest(
      upstream.url,
      new Request('http://frontend.test/health', {
        headers: { [DESKTOP_CHALLENGE_HEADER]: CHALLENGE },
      }),
      ENVIRONMENT,
    )
    assert.equal(response.status, 503)
    assert.equal(response.headers.get(DESKTOP_PROOF_HEADER), null)
  } finally {
    await upstream.close()
  }
})

test('ordinary health and API requests receive server-side identity without a proof oracle', async () => {
  const seen: string[] = []
  const upstream = await startUpstream((request, response) => {
    seen.push(String(request.headers[DESKTOP_INSTANCE_HEADER] ?? ''))
    response.writeHead(200)
    response.end('ok')
  })
  try {
    const health = await proxyRuntimeHealthRequest(
      upstream.url,
      new Request('http://frontend.test/health'),
      ENVIRONMENT,
    )
    const api = await proxyRuntimeRequest(
      upstream.url,
      new Request('http://frontend.test/api/v1/owner'),
      ENVIRONMENT,
    )
    assert.deepEqual(seen, [TOKEN, TOKEN])
    assert.equal(health.headers.get(DESKTOP_PROOF_HEADER), null)
    assert.equal(api.headers.get(DESKTOP_PROOF_HEADER), null)
  } finally {
    await upstream.close()
  }
})

test('malformed challenge and malformed runtime token fail closed', async () => {
  const invalidChallenge = await proxyRuntimeHealthRequest(
    'http://127.0.0.1:1',
    new Request('http://frontend.test/health', {
      headers: { [DESKTOP_CHALLENGE_HEADER]: 'invalid' },
    }),
    ENVIRONMENT,
  )
  assert.equal(invalidChallenge.status, 400)

  const invalidToken = await proxyRuntimeRequest(
    'http://127.0.0.1:1',
    new Request('http://frontend.test/api/v1/owner'),
    { OMNIBASE_DESKTOP_INSTANCE_TOKEN: 'invalid' },
  )
  assert.equal(invalidToken.status, 503)
})
