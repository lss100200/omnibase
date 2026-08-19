import { NextRequest } from 'next/server'

import { proxyRuntimeRequest } from '@/lib/desktop-runtime'

export const dynamic = 'force-dynamic'

const TARGET = process.env.API_PROXY_URL || 'http://backend:8000'

export function GET(request: NextRequest) {
  return proxyRuntimeRequest(TARGET, request, process.env)
}
