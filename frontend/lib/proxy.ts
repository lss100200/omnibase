/**
 * Streaming reverse-proxy core for the OmniBase API.
 *
 * The route handler at `app/api/v1/[...path]/route.ts` is a thin Next.js
 * binding over this pure, Node-testable function.  Next.js `rewrites` buffer
 * the upstream response body, which breaks Server-Sent Events; returning the
 * upstream body as a web stream keeps SSE chunks (and cancel propagation)
 * live.  Hop-by-hop headers are stripped on both the request and the
 * response, the caller's AbortSignal is bound to the upstream fetch, and an
 * upstream failure surfaces a stable 502 that never leaks the internal
 * target address.
 */

export const HOP_BY_HOP_HEADERS = [
  'connection',
  'keep-alive',
  'proxy-authenticate',
  'proxy-authorization',
  'te',
  'trailer',
  'transfer-encoding',
  'upgrade',
] as const

export function stripHopByHopHeaders(headers: Headers): void {
  // Read the Connection header FIRST: headers named by it are hop-by-hop
  // too (RFC 9110 section 7.6.1), and the loop below deletes Connection.
  const connection = headers.get('connection')
  if (connection) {
    for (const token of connection.split(',')) {
      const name = token.trim().toLowerCase()
      if (name) headers.delete(name)
    }
  }
  for (const name of HOP_BY_HOP_HEADERS) {
    headers.delete(name)
  }
}

const FORWARDED_METHODS = new Set(['GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'HEAD', 'OPTIONS'])

function upstreamUnavailable(): Response {
  // Stable, secret-free error: never echo the internal target address.
  return new Response(JSON.stringify({ error: { code: 'upstream_unavailable' } }), {
    status: 502,
    headers: { 'Content-Type': 'application/json' },
  })
}

function logUpstreamFailure(error: unknown): void {
  const name = error instanceof Error ? error.name : 'UnknownError'
  const cause = error instanceof Error ? (error.cause as { code?: unknown } | undefined) : undefined
  const rawCode = cause?.code
  const code =
    typeof rawCode === 'string' && /^[A-Z0-9_]{1,40}$/.test(rawCode) ? rawCode : 'UNCLASSIFIED'
  // Keep diagnostics useful without logging the target URL, request headers,
  // body, credentials or the provider/network error message.
  console.error(JSON.stringify({ event: 'proxy.upstream_failed', name, code }))
}

export async function proxyRequest(target: string, request: Request): Promise<Response> {
  if (!FORWARDED_METHODS.has(request.method)) {
    return new Response(JSON.stringify({ error: { code: 'method_not_allowed' } }), {
      status: 405,
      headers: { 'Content-Type': 'application/json' },
    })
  }
  const { pathname, search } = new URL(request.url)
  const upstreamUrl = `${target}${pathname}${search}`
  const headers = new Headers(request.headers)
  // The upstream must never see the Next.js host, hop-by-hop machinery or a
  // stale content-length; content-length is recomputed for the forwarded
  // body.  Authorization, Idempotency-Key and Content-Type pass through.
  stripHopByHopHeaders(headers)
  headers.delete('host')
  headers.delete('content-length')
  // Node/undici deliberately rejects Expect: 100-continue with
  // UND_ERR_NOT_SUPPORTED. Some desktop HTTP clients add it automatically;
  // the proxy buffers the small request body first, so forwarding Expect is
  // unnecessary and would turn otherwise valid POSTs into a 502.
  headers.delete('expect')
  // Compression contract (P5.4D Round 2 P2-1): we force `identity` so the
  // undici fetch never auto-decompresses the body.  A body that arrives
  // decompressed under a stale Content-Encoding/Content-Length pair would
  // be forwarded mis-framed; the contract below prevents that entirely.
  headers.set('accept-encoding', 'identity')
  let upstream: Response
  try {
    upstream = await fetch(upstreamUrl, {
      method: request.method,
      headers,
      // Request bodies are small JSON payloads, so buffering them is fine;
      // only the RESPONSE must stay streaming (SSE).  The caller's
      // AbortSignal is bound so a cancelled client also cancels upstream.
      body:
        request.method === 'GET' || request.method === 'HEAD'
          ? undefined
          : await request.arrayBuffer(),
      signal: request.signal,
    })
  } catch (error) {
    if (request.signal.aborted) {
      // The client cancelled; rethrow so the caller observes the abort
      // instead of a misleading 502.
      throw error
    }
    logUpstreamFailure(error)
    return upstreamUnavailable()
  }
  const responseHeaders = new Headers(upstream.headers)
  stripHopByHopHeaders(responseHeaders)
  // If the upstream ignored `Accept-Encoding: identity` and still sent a
  // compressed encoding, fail closed instead of forwarding decompressed
  // bytes under the stale compression header (no double-decode surprises
  // and no wrong Content-Length waits for the browser).
  const contentEncoding = (responseHeaders.get('content-encoding') ?? '').trim().toLowerCase()
  if (contentEncoding !== '' && contentEncoding !== 'identity') {
    return upstreamUnavailable()
  }
  // Server-Sent Events must not be buffered by any hop.
  responseHeaders.set('X-Accel-Buffering', 'no')
  return new Response(upstream.body, {
    status: upstream.status,
    headers: responseHeaders,
  })
}
