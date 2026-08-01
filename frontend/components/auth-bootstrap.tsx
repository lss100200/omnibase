'use client'

import { useEffect } from 'react'
import { bootstrapAuth } from '@/lib/auth-bootstrap'

const RETRY_DELAY_MS = 15_000

export function AuthBootstrap() {
  useEffect(() => {
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
