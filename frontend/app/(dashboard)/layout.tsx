'use client'

import { useEffect } from 'react'
import { useRouter } from 'next/navigation'
import { useAuth } from '@/lib/hooks/use-auth'
import { Sidebar } from '@/components/layout/sidebar'
import { UserMenu } from '@/components/layout/user-menu'
import { ThemeToggle } from '@/components/theme-toggle'

/**
 * Authenticated shell.
 *
 * - Redirects to /login if no session
 * - Renders sidebar + top bar + main content area
 * - Top bar holds the user menu (logout etc.)
 */
export default function DashboardLayout({ children }: { children: React.ReactNode }) {
  const router = useRouter()
  const { isAuthenticated, bootstrapStatus } = useAuth()

  useEffect(() => {
    if (bootstrapStatus === 'ready' && !isAuthenticated) {
      const returnPath = `${window.location.pathname}${window.location.search}${window.location.hash}`
      router.replace(`/login?from=${encodeURIComponent(returnPath)}`)
    }
  }, [router, isAuthenticated, bootstrapStatus])

  let content: React.ReactNode
  if (bootstrapStatus === 'unavailable') {
    content = (
      <div className="flex h-full items-center justify-center">
        <div className="max-w-md space-y-2 text-center">
          <div className="font-medium">暂时无法验证登录状态</div>
          <div className="text-sm text-muted-foreground">
            已保留本地会话，网络恢复后会自动重试。
          </div>
        </div>
      </div>
    )
  } else if (bootstrapStatus === 'pending' || !isAuthenticated) {
    content = (
      <div className="flex h-full items-center justify-center">
        <div className="text-muted-foreground">正在加载工作台…</div>
      </div>
    )
  } else {
    content = children
  }

  return (
    <div className="flex h-screen overflow-hidden bg-background">
      <Sidebar />
      <div className="flex flex-1 flex-col overflow-hidden">
        {/* Top bar */}
        <header className="flex h-16 items-center justify-between border-b bg-card px-6">
          <div className="flex items-center gap-2 text-sm text-muted-foreground">
            <span>OmniBase</span>
            <span className="text-border">/</span>
          </div>
          <div className="flex items-center gap-1">
            <ThemeToggle />
            <UserMenu />
          </div>
        </header>

        {/* Main content */}
        <main className="flex-1 overflow-auto p-6">{content}</main>
      </div>
    </div>
  )
}
