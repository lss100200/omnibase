'use client'

import Link from 'next/link'
import { usePathname } from 'next/navigation'
import { BookOpen, Database, LayoutDashboard, MessageSquare, Settings, Sparkles } from 'lucide-react'
import { cn } from '@/lib/utils'

const navItems = [
  { href: '/dashboard', label: '概览', icon: LayoutDashboard },
  { href: '/knowledge', label: '知识库', icon: BookOpen },
  { href: '/playground', label: '检索测试', icon: Sparkles },
  { href: '/chat', label: 'AI 问答', icon: MessageSquare },
  { href: '/database', label: '数据库', icon: Database },
  { href: '/settings', label: '设置', icon: Settings },
] as const

export function Sidebar() {
  const pathname = usePathname()

  return (
    <aside className="flex h-full w-60 flex-col border-r bg-card">
      {/* Brand */}
      <div className="flex h-16 items-center gap-2 border-b px-6">
        <div className="flex h-8 w-8 items-center justify-center rounded-md bg-primary text-primary-foreground">
          <Sparkles className="h-5 w-5" />
        </div>
        <span className="text-lg font-semibold tracking-tight">OmniBase</span>
      </div>

      {/* Nav */}
      <nav className="flex-1 space-y-1 p-3">
        {navItems.map((item) => {
          const Icon = item.icon
          const active = pathname === item.href || pathname.startsWith(item.href + '/')
          return (
            <Link
              key={item.href}
              href={item.href}
              className={cn(
                'flex items-center gap-3 rounded-md px-3 py-2 text-sm font-medium transition-colors',
                active
                  ? 'bg-primary text-primary-foreground'
                  : 'text-muted-foreground hover:bg-accent hover:text-accent-foreground',
              )}
            >
              <Icon className="h-4 w-4" />
              {item.label}
            </Link>
          )
        })}
      </nav>

      {/* Footer status */}
      <div className="border-t p-3">
        <div className="flex items-center gap-2 rounded-md bg-muted/50 px-3 py-2 text-xs text-muted-foreground">
          <span className="h-2 w-2 rounded-full bg-success" />
          Phase 0 · 开发中
        </div>
      </div>
    </aside>
  )
}
