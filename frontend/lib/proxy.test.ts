import assert from 'node:assert/strict'
import http from 'node:http'
import type { AddressInfo } from 'node:net'
import { test } from 'node:test'

import { HOP_BY_HOP_HEADERS, proxyRequest, stripHopByHopHeaders } from './proxy'

function startUpstream(
  handler: (req: http.IncomingMessage, res: http.ServerResponse) => void,
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

test('SSE body is streamed per chunk, not buffered', async () => {
  const upstream = await startUpstream((req, res) => {
    res.writeHead(200, { 'Content-Type': 'text/event-stream' })
    res.write('event: chunk\ndata: {"content":"first"}\n\n')
    res.flushHeaders()
    setTimeout(() => {
      res.write('event: chunk\ndata: {"content":"second"}\n\n')
      res.end()
    }, 300)
  })
  try {
    const response = await proxyRequest(
      upstream.url,
      new Request('http://frontend.test/api/v1/stream', { method: 'POST' }),
    )
    assert.equal(response.status, 200)
    assert.equal(response.headers.get('content-type'), 'text/event-stream')
    const reader = response.body!.getReader()
    const decoder = new TextDecoder()
    const started = Date.now()
    const first = await reader.read()
    const firstAt = Date.now() - started
    assert.equal(decoder.decode(first.value).includes('first'), true)
    // The first chunk must arrive well before the second is emitted.
    const second = await reader.read()
    const secondAt = Date.now() - started
    assert.equal(decoder.decode(second.value).includes('second'), true)
    assert.equal(secondAt >= firstAt + 150, true, 'chunks must arrive separately')
    assert.equal(firstAt < 250, true, 'first chunk must not be buffered until the end')
  } finally {
    await upstream.close()
  }
})

test('abort propagates to the upstream fetch', async () => {
  const upstream = await startUpstream((req, res) => {
    res.writeHead(200, { 'Content-Type': 'text/event-stream' })
    res.write('event: chunk\ndata: {"content":"x"}\n\n')
    // never end; the client aborts mid-stream
  })
  try {
    const controller = new AbortController()
    const response = await proxyRequest(
      upstream.url,
      new Request('http://frontend.test/api/v1/stream', {
        method: 'POST',
        signal: controller.signal,
      }),
    )
    const reader = response.body!.getReader()
    await reader.read()
    controller.abort()
    await assert.rejects(
      () => reader.read(),
      (error: unknown) => {
        assert.equal(error instanceof DOMException && error.name === 'AbortError', true)
        return true
      },
    )
  } finally {
    await upstream.close()
  }
})

test('method and body are forwarded; hop-by-hop headers are stripped', async () => {
  let seenMethod = ''
  let seenBody = ''
  let seenAuth = ''
  let seenIdem = ''
  let seenHop = ''
  const upstream = await startUpstream((req, res) => {
    seenMethod = req.method ?? ''
    seenAuth = String(req.headers.authorization ?? '')
    seenIdem = String(req.headers['idempotency-key'] ?? '')
    seenHop = String(req.headers['transfer-encoding'] ?? '')
    let body = ''
    req.on('data', (chunk) => {
      body += chunk
    })
    req.on('end', () => {
      seenBody = body
      res.writeHead(201, { 'Content-Type': 'application/json' })
      res.end('{"ok":true}')
    })
  })
  try {
    const headers = new Headers({
      Authorization: 'Bearer secret-token',
      'Idempotency-Key': 'key-123',
      'Content-Type': 'application/json',
      'Transfer-Encoding': 'chunked',
      Connection: 'keep-alive, te',
      TE: 'trailers',
    })
    const response = await proxyRequest(
      upstream.url,
      new Request('http://frontend.test/api/v1/workspaces/w/agent-alpha/invoke', {
        method: 'POST',
        headers,
        body: JSON.stringify({ message: 'hi' }),
      }),
    )
    assert.equal(response.status, 201)
    assert.equal(seenMethod, 'POST')
    assert.equal(seenBody, '{"message":"hi"}')
    assert.equal(seenAuth, 'Bearer secret-token')
    assert.equal(seenIdem, 'key-123')
    assert.equal(seenHop, '', 'transfer-encoding must be stripped from the forwarded request')
  } finally {
    await upstream.close()
  }
})

test('response status/content-type pass through and hop-by-hop is stripped', async () => {
  const upstream = await startUpstream((req, res) => {
    res.writeHead(404, {
      'Content-Type': 'application/json',
      'Transfer-Encoding': 'chunked',
      Connection: 'close',
    })
    res.end('{"error":"nope"}')
  })
  try {
    const response = await proxyRequest(
      upstream.url,
      new Request('http://frontend.test/api/v1/missing', { method: 'GET' }),
    )
    assert.equal(response.status, 404)
    assert.equal(response.headers.get('content-type'), 'application/json')
    assert.equal(response.headers.get('transfer-encoding'), null)
    assert.equal(response.headers.get('connection'), null)
    assert.equal(response.headers.get('x-accel-buffering'), 'no')
  } finally {
    await upstream.close()
  }
})

test('unsupported methods get 405', async () => {
  const response = await proxyRequest(
    'http://127.0.0.1:1',
    new Request('http://frontend.test/api/v1/x', { method: 'PURGE' }),
  )
  assert.equal(response.status, 405)
})

test('upstream unavailable returns a stable 502 without leaking the target', async () => {
  const response = await proxyRequest(
    'http://127.0.0.1:1',
    new Request('http://frontend.test/api/v1/x', { method: 'GET' }),
  )
  assert.equal(response.status, 502)
  const payload = (await response.json()) as { error?: { code?: string } }
  assert.equal(payload.error?.code, 'upstream_unavailable')
  assert.equal(JSON.stringify(payload).includes('127.0.0.1'), false)
})

test('stripHopByHopHeaders removes Connection-named headers too', () => {
  const headers = new Headers({
    Connection: 'keep-alive, X-Custom-Hop',
    'X-Custom-Hop': 'secret',
    'Keep-Alive': 'timeout=5',
    'X-Keep': 'keep-me',
  })
  stripHopByHopHeaders(headers)
  assert.equal(headers.get('connection'), null)
  assert.equal(headers.get('keep-alive'), null)
  assert.equal(headers.get('x-custom-hop'), null)
  assert.equal(headers.get('x-keep'), 'keep-me')
  for (const name of HOP_BY_HOP_HEADERS) {
    assert.equal(headers.has(name), false)
  }
})

// ---------------------------------------------------------------------------
// Compression contract (P5.4D Round 2 P2-1)
// ---------------------------------------------------------------------------

import zlib from 'node:zlib'

function startEncodingUpstream(
  encoding: string | null,
  honorIdentity: boolean,
): Promise<{ url: string; close: () => Promise<void>; seenAcceptEncoding: () => string | null }> {
  let seenAcceptEncoding: string | null = null
  return new Promise((resolve) => {
    const server = http.createServer((req, res) => {
      seenAcceptEncoding = String(req.headers['accept-encoding'] ?? '') || null
      const payload = Buffer.from('{"hello":"world","value":"p52d-acceptance"}')
      if (encoding === null || (encoding === 'identity' && honorIdentity)) {
        res.writeHead(200, { 'Content-Type': 'application/json' })
        res.end(payload)
        return
      }
      if (honorIdentity && seenAcceptEncoding === 'identity') {
        res.writeHead(200, { 'Content-Type': 'application/json' })
        res.end(payload)
        return
      }
      // The upstream ignores Accept-Encoding and compresses anyway.
      const compressed =
        encoding === 'gzip'
          ? zlib.gzipSync(payload)
          : encoding === 'br'
            ? zlib.brotliCompressSync(payload)
            : zlib.deflateSync(payload)
      res.writeHead(200, {
        'Content-Type': 'application/json',
        'Content-Encoding': encoding,
        'Content-Length': String(compressed.length),
      })
      res.end(compressed)
    })
    server.listen(0, '127.0.0.1', () => {
      const { port } = server.address() as AddressInfo
      resolve({
        url: `http://127.0.0.1:${port}`,
        close: () => new Promise((done) => server.close(() => done())),
        seenAcceptEncoding: () => seenAcceptEncoding,
      })
    })
  })
}

test('gzip upstream is failed closed, never forwarded decompressed', async () => {
  const upstream = await startEncodingUpstream('gzip', false)
  try {
    const response = await proxyRequest(
      upstream.url,
      new Request('http://frontend.test/api/v1/x', { method: 'GET' }),
    )
    assert.equal(response.status, 502, 'compressed body must fail closed')
    assert.equal(response.headers.get('content-encoding'), null)
  } finally {
    await upstream.close()
  }
})

test('brotli upstream is failed closed', async () => {
  const upstream = await startEncodingUpstream('br', false)
  try {
    const response = await proxyRequest(
      upstream.url,
      new Request('http://frontend.test/api/v1/x', { method: 'GET' }),
    )
    assert.equal(response.status, 502)
  } finally {
    await upstream.close()
  }
})

test('deflate upstream is failed closed', async () => {
  const upstream = await startEncodingUpstream('deflate', false)
  try {
    const response = await proxyRequest(
      upstream.url,
      new Request('http://frontend.test/api/v1/x', { method: 'GET' }),
    )
    assert.equal(response.status, 502)
  } finally {
    await upstream.close()
  }
})

test('identity is requested; a compliant upstream body passes through byte-clean', async () => {
  const upstream = await startEncodingUpstream('gzip', true)
  try {
    const response = await proxyRequest(
      upstream.url,
      new Request('http://frontend.test/api/v1/x', { method: 'GET' }),
    )
    assert.equal(upstream.seenAcceptEncoding(), 'identity')
    assert.equal(response.status, 200)
    assert.equal(response.headers.get('content-encoding'), null)
    const body = await response.text()
    assert.deepEqual(JSON.parse(body), { hello: 'world', value: 'p52d-acceptance' })
  } finally {
    await upstream.close()
  }
})

test('identity header on the response is forwarded verbatim', async () => {
  const upstream = await startEncodingUpstream('identity', true)
  try {
    const response = await proxyRequest(
      upstream.url,
      new Request('http://frontend.test/api/v1/x', { method: 'GET' }),
    )
    assert.equal(response.status, 200)
    const body = await response.text()
    assert.deepEqual(JSON.parse(body), { hello: 'world', value: 'p52d-acceptance' })
  } finally {
    await upstream.close()
  }
})
