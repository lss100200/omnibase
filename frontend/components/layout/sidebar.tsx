'use client'

import Link from 'next/link'
import { usePathname } from 'next/navigation'
import {
  BookOpen,
  Bot,
  Boxes,
  CircleDot,
  Database,
  FileSearch,
  Plug,
  Settings,
  Sparkles,
  Store,
  Wrench,
} from 'lucide-react'
import { BrandLockup } from '@/components/layout/brand-mark'
import { cn } from '@/lib/utils'

export const navGroups = [
  {
    label: '工作区',
    items: [
      { href: '/dashboard', label: 'AI 工作台', icon: Sparkles },
      { href: '/spaces', label: 'Workspaces', icon: Boxes },
      { href: '/knowledge', label: '知识与文件', icon: BookOpen },
      { href: '/database', label: '数据库', icon: Database },
      { href: '/playground', label: '检索实验室', icon: FileSearch },
    ],
  },
  {
    label: '扩展',
    items: [
      { href: '/agents', label: 'AI 员工', icon: Bot },
      { label: 'Skills', icon: Wrench, status: 'preview' },
      { label: 'MCP', icon: Plug, status: 'locked' },
      { label: '市场', icon: Store, status: 'preview' },
    ],
  },
  {
    label: '系统',
    items: [{ href: '/settings', label: '设置', icon: Settings }],
  },
] as const

export function Sidebar() {
  const pathname = usePathname()

  return (
    <aside className="relative hidden h-full w-64 shrink-0 flex-col overflow-hidden border-r border-border bg-background text-foreground lg:flex">
      <div className="relative flex h-16 items-center border-b border-border px-4">
        <div className="text-foreground">
          <BrandLockup />
        </div>
      </div>

      <nav className="relative flex-1 space-y-5 overflow-y-auto px-3 py-5">
        {navGroups.map((group) => (
          <div key={group.label}>
            <div className="mb-2 px-3 font-mono text-[8px] font-semibold uppercase tracking-[0.22em] text-muted-foreground">
              {group.label}
            </div>
            <div className="space-y-0.5">
              {group.items.map((item) => {
                const Icon = item.icon
                if (!('href' in item)) {
                  return (
                    <div
                      key={item.label}
                      aria-disabled="true"
                      className="flex h-10 items-center gap-2.5 rounded-lg px-3 text-xs text-muted-foreground"
                    >
                      <span className="flex h-7 w-7 items-center justify-center rounded-lg border border-border bg-muted/35">
                        <Icon className="h-3.5 w-3.5" />
                      </span>
                      <span className="flex-1">{item.label}</span>
                      <span
                        className={cn(
                          'rounded-full px-1.5 py-0.5 font-mono text-[6px] uppercase tracking-wider',
                          item.status === 'preview'
                            ? 'border border-foreground/25 bg-foreground/10 text-foreground'
                            : 'border border-border bg-transparent text-muted-foreground',
                        )}
                      >
                        {item.status}
                      </span>
                    </div>
                  )
                }

                const active = pathname === item.href || pathname.startsWith(item.href + '/')
                return (
                  <Link
                    key={item.href}
                    href={item.href}
                    className={cn(
                      'group relative flex h-10 items-center gap-2.5 overflow-hidden rounded-lg px-3 text-xs font-medium transition-colors',
                      active
                        ? 'bg-foreground text-background'
                        : 'text-muted-foreground hover:bg-muted hover:text-foreground',
                    )}
                  >
                    {active && (
                      <span className="absolute bottom-2 left-0 top-2 w-0.5 bg-background" />
                    )}
                    <span
                      className={cn(
                        'flex h-7 w-7 items-center justify-center border transition-colors',
                        active
                          ? 'border-background/20 bg-background/10 text-background'
                          : 'border-border bg-muted/35 text-muted-foreground group-hover:text-foreground',
                      )}
                    >
                      <Icon className="h-3.5 w-3.5" />
                    </span>
                    {item.label}
                  </Link>
                )
              })}
            </div>
          </div>
        ))}
      </nav>

      <div className="relative border-t border-border p-3">
        <div className="rounded-xl border border-border bg-muted/35 px-3 py-3">
          <div className="flex items-center gap-2">
            <CircleDot className="h-3.5 w-3.5 text-foreground" />
            <span className="text-[10px] font-semibold text-foreground">Self-hosted workspace</span>
          </div>
          <div className="mt-2 flex items-center justify-between font-mono text-[7px] uppercase tracking-wider text-muted-foreground">
            <span>Agent Alpha</span>
            <span className="text-foreground">Tool-free</span>
          </div>
        </div>
      </div>
    </aside>
  )
}
