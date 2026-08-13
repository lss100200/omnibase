'use client'

import { useEffect } from 'react'
import Link from 'next/link'
import { Menu, ShieldCheck } from 'lucide-react'
import { usePathname, useRouter } from 'next/navigation'
import { useAuth } from '@/lib/hooks/use-auth'
import { Sidebar } from '@/components/layout/sidebar'
import { UserMenu } from '@/components/layout/user-menu'
import { ThemeToggle } from '@/components/theme-toggle'
import { BrandLockup } from '@/components/layout/brand-mark'
import { navGroups } from '@/components/layout/sidebar'
import { Button } from '@/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'

const pageTitles: Record<string, string> = {
  '/dashboard': 'AI 工作台',
  '/spaces': 'AI 空间',
  '/knowledge': '知识资产',
  '/playground': '检索实验室',
  '/chat': 'AI 问答',
  '/agents': 'AI 员工',
  '/skills': '原生技能',
  '/database': '数据库能力',
  '/settings': '系统设置',
}

/**
 * Authenticated shell.
 *
 * - Redirects to /login if no session
 * - Renders sidebar + top bar + main content area
 * - Top bar holds the user menu (logout etc.)
 */
export default function DashboardLayout({ children }: { children: React.ReactNode }) {
  const router = useRouter()
  const pathname = usePathname()
  const { isAuthenticated, bootstrapStatus } = useAuth()
  const basePath = `/${pathname.split('/')[1] ?? 'dashboard'}`
  const pageTitle = pageTitles[basePath] ?? 'AI 工作台'

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
        <header className="bg-background/94 relative z-20 flex h-16 shrink-0 items-center justify-between border-b border-border/90 px-4 backdrop-blur sm:px-5">
          <div className="flex min-w-0 items-center gap-3">
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button variant="outline" size="icon" className="lg:hidden" aria-label="打开导航">
                  <Menu />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="start" className="w-64">
                <DropdownMenuLabel>
                  <BrandLockup />
                </DropdownMenuLabel>
                <DropdownMenuSeparator />
                {navGroups.flatMap((group) =>
                  group.items.map((item) => {
                    const Icon = item.icon
                    if (!('href' in item)) {
                      return (
                        <DropdownMenuItem key={item.label} disabled>
                          <Icon className="h-4 w-4" />
                          {item.label}
                          <span className="ml-auto font-mono text-xs uppercase">{item.status}</span>
                        </DropdownMenuItem>
                      )
                    }
                    return (
                      <DropdownMenuItem key={item.href} asChild>
                        <Link href={item.href} className="flex items-center gap-2 py-2">
                          <Icon className="h-4 w-4" />
                          {item.label}
                        </Link>
                      </DropdownMenuItem>
                    )
                  }),
                )}
              </DropdownMenuContent>
            </DropdownMenu>
            <div className="min-w-0">
              <div className="font-mono text-xs font-semibold uppercase tracking-[0.18em] text-muted-foreground">
                OmniBase / 工作会话
              </div>
              <div className="truncate text-base font-semibold tracking-tight sm:text-lg">
                {pageTitle}
              </div>
            </div>
          </div>
          <div className="flex items-center gap-1 sm:gap-2">
            <div className="hidden items-center gap-2 border border-border/70 bg-card/55 px-3 py-1.5 xl:flex">
              <ShieldCheck className="h-3.5 w-3.5 text-foreground" />
              <span className="font-mono text-xs uppercase tracking-[0.14em] text-muted-foreground">
                受治理的工作空间
              </span>
            </div>
            <ThemeToggle />
            <UserMenu />
          </div>
        </header>

        {/* Main content */}
        <main className="relative z-10 flex-1 overflow-auto">
          <div className="w-full p-3 sm:p-4">{content}</div>
        </main>
      </div>
    </div>
  )
}
