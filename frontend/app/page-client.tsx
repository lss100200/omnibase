'use client'

import { useEffect } from 'react'
import { useRouter } from 'next/navigation'
import { useAuth } from '@/lib/hooks/use-auth'
import { Skeleton } from '@/components/ui/skeleton'

/**
 * Client-side redirect based on auth state.
 * Mounts after hydration so we can read localStorage safely.
 */
export default function HomeRedirect() {
  const router = useRouter()
  const { isAuthenticated, bootstrapStatus } = useAuth()

  useEffect(() => {
    // Only redirect once bootstrap has definitively resolved.
    // 'unavailable' means transient failure — keep showing the landing skeleton.
    if (bootstrapStatus !== 'ready') return
    router.replace(isAuthenticated ? '/dashboard' : '/login')
  }, [router, isAuthenticated, bootstrapStatus])

  return (
    <main className="flex min-h-screen items-center justify-center p-8">
      <div className="w-full max-w-sm space-y-4">
        <Skeleton className="h-12 w-full" />
        <Skeleton className="h-4 w-3/4" />
        <Skeleton className="h-4 w-1/2" />
        <p className="text-center text-sm text-muted-foreground">正在进入 OmniBase…</p>
      </div>
    </main>
  )
}
