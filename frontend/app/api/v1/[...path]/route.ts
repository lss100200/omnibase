import { NextRequest } from 'next/server'

import { proxyRuntimeRequest } from '@/lib/desktop-runtime'

/**
 * Streaming reverse proxy for the OmniBase API.
 *
 * Next.js `rewrites` buffer the upstream response body, which breaks
 * Server-Sent Events: the Agent workbench would only receive the whole stream
 * after the backend finished, so "streaming" answers and mid-stream cancel
 * never surfaced in the UI.  This Route Handler returns the upstream body as
 * a web stream, so SSE chunks (and any future streaming endpoint) arrive in
 * real time.  All paths under /api/v1 are forwarded with their original
 * method and headers (Authorization, Idempotency-Key, ...); the proxy core
 * lives in `lib/proxy.ts` so its boundary behavior is unit-tested.  Do not
 * restore an `/api/:path*` rewrite — it buffers the body and breaks streaming.
 */

export const dynamic = 'force-dynamic'

const TARGET = process.env.API_PROXY_URL || 'http://backend:8000'

function proxy(request: NextRequest) {
  return proxyRuntimeRequest(TARGET, request, process.env)
}

export function GET(request: NextRequest) {
  return proxy(request)
}

export function POST(request: NextRequest) {
  return proxy(request)
}

export function PUT(request: NextRequest) {
  return proxy(request)
}

export function PATCH(request: NextRequest) {
  return proxy(request)
}

export function DELETE(request: NextRequest) {
  return proxy(request)
}

export function HEAD(request: NextRequest) {
  return proxy(request)
}

export function OPTIONS(request: NextRequest) {
  return proxy(request)
}
