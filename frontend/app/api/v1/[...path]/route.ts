import { NextRequest } from 'next/server'

/**
 * Streaming reverse proxy for the OmniBase API.
 *
 * Next.js `rewrites` (used previously) buffer the upstream response body, which
 * breaks Server-Sent Events: the Agent workbench would only receive the whole
 * stream after the backend finished, so "streaming" answers and mid-stream
 * cancel never surfaced in the UI.  A Route Handler returns the upstream
 * `Response` body as a web stream, so SSE chunks (and any future streaming
 * endpoint) arrive in real time.  All paths under /api/v1 are forwarded with
 * their original method and headers (Authorization, Idempotency-Key, ...).
 */

export const dynamic = 'force-dynamic'

const TARGET = process.env.API_PROXY_URL || 'http://backend:8000'

async function proxy(request: NextRequest, method: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE') {
  const { pathname, search } = new URL(request.url)
  const upstreamUrl = `${TARGET}${pathname}${search}`
  const headers = new Headers(request.headers)
  // The upstream must never see the Next.js host; content-length is recomputed
  // by the underlying fetch for the forwarded body.
  headers.delete('host')
  headers.delete('content-length')
  let upstream: Response
  try {
    upstream = await fetch(upstreamUrl, {
      method,
      headers,
      // Request bodies are small JSON payloads, so buffering them is fine;
      // only the RESPONSE must stay streaming (SSE).
      body: method === 'GET' ? undefined : await request.arrayBuffer(),
    })
  } catch {
    return new Response(JSON.stringify({ error: { code: 'upstream_unavailable' } }), {
      status: 502,
      headers: { 'Content-Type': 'application/json' },
    })
  }
  const responseHeaders = new Headers(upstream.headers)
  // Server-Sent Events must not be buffered by any hop.
  responseHeaders.set('X-Accel-Buffering', 'no')
  return new Response(upstream.body, {
    status: upstream.status,
    headers: responseHeaders,
  })
}

export function GET(request: NextRequest) {
  return proxy(request, 'GET')
}

export function POST(request: NextRequest) {
  return proxy(request, 'POST')
}

export function PUT(request: NextRequest) {
  return proxy(request, 'PUT')
}

export function PATCH(request: NextRequest) {
  return proxy(request, 'PATCH')
}

export function DELETE(request: NextRequest) {
  return proxy(request, 'DELETE')
}
