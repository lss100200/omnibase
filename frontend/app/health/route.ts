import { NextRequest } from 'next/server'

import { proxyRuntimeHealthRequest } from '@/lib/desktop-runtime'

export const dynamic = 'force-dynamic'

const TARGET = process.env.API_PROXY_URL || 'http://backend:8000'

export function GET(request: NextRequest) {
  return proxyRuntimeHealthRequest(TARGET, request, process.env)
}
