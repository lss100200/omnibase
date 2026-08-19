'use client'

import { useEffect } from 'react'
import { bootstrapAuth } from '@/lib/auth-bootstrap'
import { getDesktopBridge } from '@/lib/desktop-bridge'

const RETRY_DELAY_MS = 15_000

export function AuthBootstrap() {
  useEffect(() => {
    if (getDesktopBridge() !== null) return

    let retryTimer: ReturnType<typeof setTimeout> | undefined
    let disposed = false

    const run = () => {
      void bootstrapAuth().catch(() => {
        if (!disposed) retryTimer = setTimeout(run, RETRY_DELAY_MS)
      })
    }
    const retryOnline = () => run()

    run()
    window.addEventListener('online', retryOnline)
    return () => {
      disposed = true
      if (retryTimer) clearTimeout(retryTimer)
      window.removeEventListener('online', retryOnline)
    }
  }, [])

  return null
}
