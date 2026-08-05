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
    <aside className="relative hidden h-full w-64 shrink-0 flex-col overflow-hidden border-r border-white/[0.06] bg-[#070b1b] text-slate-100 lg:flex">
      <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(circle_at_0%_0%,rgba(99,102,241,.16),transparent_18rem),radial-gradient(circle_at_100%_40%,rgba(34,211,238,.07),transparent_18rem)]" />
      <div className="relative flex h-16 items-center border-b border-white/[0.07] px-4">
        <BrandLockup />
      </div>

      <nav className="relative flex-1 space-y-5 overflow-y-auto px-3 py-5">
        {navGroups.map((group) => (
          <div key={group.label}>
            <div className="mb-2 px-3 font-mono text-[8px] font-semibold uppercase tracking-[0.22em] text-slate-600">
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
                      className="flex h-10 items-center gap-2.5 rounded-lg px-3 text-xs text-slate-600"
                    >
                      <span className="flex h-7 w-7 items-center justify-center rounded-lg border border-white/[0.06] bg-white/[0.025]">
                        <Icon className="h-3.5 w-3.5" />
                      </span>
                      <span className="flex-1">{item.label}</span>
                      <span
                        className={cn(
                          'rounded-full px-1.5 py-0.5 font-mono text-[6px] uppercase tracking-wider',
                          item.status === 'preview'
                            ? 'bg-indigo-400/10 text-indigo-300'
                            : 'bg-amber-400/10 text-amber-300',
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
                        ? 'bg-white/[0.09] text-white shadow-[inset_0_1px_0_rgba(255,255,255,.06),0_12px_30px_-22px_rgba(99,102,241,.9)]'
                        : 'text-slate-500 hover:bg-white/[0.045] hover:text-slate-200',
                    )}
                  >
                    {active && (
                      <span className="absolute bottom-2 left-0 top-2 w-0.5 rounded-full bg-gradient-to-b from-indigo-400 to-cyan-400" />
                    )}
                    <span
                      className={cn(
                        'flex h-7 w-7 items-center justify-center border transition-colors',
                        active
                          ? 'border-indigo-300/20 bg-indigo-400/10 text-indigo-200'
                          : 'border-white/[0.06] bg-white/[0.025] text-slate-600 group-hover:text-slate-200',
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

      <div className="relative border-t border-white/[0.07] p-3">
        <div className="rounded-xl border border-white/[0.07] bg-white/[0.025] px-3 py-3">
          <div className="flex items-center gap-2">
            <CircleDot className="h-3.5 w-3.5 text-emerald-400" />
            <span className="text-[10px] font-semibold text-slate-300">Self-hosted workspace</span>
          </div>
          <div className="mt-2 flex items-center justify-between font-mono text-[7px] uppercase tracking-wider text-slate-600">
            <span>Agent Alpha</span>
            <span className="text-emerald-300">Tool-free</span>
          </div>
        </div>
      </div>
    </aside>
  )
}
