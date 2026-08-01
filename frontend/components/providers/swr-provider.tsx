'use client'

import { SWRConfig } from 'swr'

function getResponseStatus(error: unknown) {
  if (!error || typeof error !== 'object' || !('response' in error)) return undefined

  return (error as { response?: { status?: number } }).response?.status
}

export function SwrProvider({ children }: { children: React.ReactNode }) {
  return (
    <SWRConfig
      value={{
        dedupingInterval: 2_000,
        focusThrottleInterval: 30_000,
        errorRetryCount: 2,
        errorRetryInterval: 3_000,
        revalidateOnFocus: true,
        revalidateOnReconnect: true,
        shouldRetryOnError: (error) => {
          const status = getResponseStatus(error)
          return status === undefined || status === 429 || status >= 500
        },
      }}
    >
      {children}
    </SWRConfig>
  )
}
