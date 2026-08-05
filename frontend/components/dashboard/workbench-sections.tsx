import { CheckCircle2, Clock3, LockKeyhole, Sparkles } from 'lucide-react'
import type { DocumentRead, WorkspaceRead } from '@/lib/types'
import { Skeleton } from '@/components/ui/skeleton'
import { cn } from '@/lib/utils'

export type Tone = 'success' | 'warning' | 'neutral' | 'primary'

export function Metric({
  label,
  value,
  hint,
}: {
  label: string
  value: number | string | null | undefined
  hint: string
}) {
  return (
    <div className="min-w-0 rounded-xl border border-border/70 bg-background/35 px-3 py-3">
      <div className="flex items-end justify-between gap-2">
        {value == null ? (
          <Skeleton className="h-6 w-12" />
        ) : (
          <span className="text-xl font-bold tracking-[-0.04em]">{value}</span>
        )}
        <span className="font-mono text-[8px] uppercase tracking-wider text-muted-foreground">
          {label}
        </span>
      </div>
      <p className="mt-1 truncate text-[10px] text-muted-foreground">{hint}</p>
    </div>
  )
}

export type OverviewItem = {
  id: string
  title: string
  detail: string
  status: string
  tone: Tone
}

export function ResourcePreview({
  items,
  loading,
  error,
  empty,
}: {
  items: OverviewItem[]
  loading: boolean
  error: boolean
  empty: string
}) {
  if (loading) {
    return (
      <div className="space-y-2">
        <Skeleton className="h-10 w-full" />
        <Skeleton className="h-10 w-full" />
      </div>
    )
  }

  if (error || items.length === 0) {
    return (
      <div className="flex h-[5.5rem] items-center justify-center rounded-xl border border-dashed border-border/60 bg-muted/10 px-3 text-center">
        <Clock3 className="mr-2 h-3.5 w-3.5 text-muted-foreground/60" />
        <span className="text-[10px] text-muted-foreground">
          {error ? '接口未连接，数据状态未知' : empty}
        </span>
      </div>
    )
  }

  return (
    <div className="space-y-1.5">
      {items.slice(0, 2).map((item) => (
        <div
          key={item.id}
          className="flex min-w-0 items-center gap-2 rounded-lg border border-border/55 bg-muted/5 px-2.5 py-2"
        >
          <span className="h-5 w-0.5 shrink-0 bg-primary" />
          <div className="min-w-0 flex-1">
            <p className="truncate text-[11px] font-medium">{item.title}</p>
            <p className="truncate text-[9px] text-muted-foreground">{item.detail}</p>
          </div>
          <StateBadge state={item.status} tone={item.tone} />
        </div>
      ))}
    </div>
  )
}

export function StateBadge({ state, tone }: { state: string; tone: Tone }) {
  const className = {
    success: 'bg-emerald-500/10 text-emerald-600 dark:text-emerald-300',
    warning: 'bg-amber-500/10 text-amber-600 dark:text-amber-300',
    primary: 'bg-primary/10 text-primary',
    neutral: 'bg-muted text-muted-foreground',
  }[tone]

  return (
    <span
      className={cn(
        'shrink-0 rounded-full px-1.5 py-0.5 font-mono text-[7px] uppercase tracking-wider',
        className,
      )}
    >
      {state}
    </span>
  )
}

export function CapabilityRow({
  icon: Icon,
  title,
  description,
  state,
  tone,
}: {
  icon: React.ComponentType<{ className?: string }>
  title: string
  description: string
  state: string
  tone: Tone
}) {
  return (
    <div className="flex items-center gap-2.5 rounded-xl border border-border/60 bg-muted/5 px-2.5 py-2.5">
      <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg border border-border bg-background/50 text-primary">
        <Icon className="h-3.5 w-3.5" />
      </div>
      <div className="min-w-0 flex-1">
        <p className="text-[11px] font-semibold">{title}</p>
        <p className="truncate text-[9px] text-muted-foreground">{description}</p>
      </div>
      <StateBadge state={state} tone={tone} />
    </div>
  )
}

export function PrincipleCard({
  icon: Icon,
  title,
  description,
  status,
}: {
  icon: React.ComponentType<{ className?: string }>
  title: string
  description: string
  status: 'available' | 'preview' | 'locked'
}) {
  const meta = {
    available: { label: 'Available', icon: CheckCircle2, className: 'text-emerald-500' },
    preview: { label: 'Preview', icon: Sparkles, className: 'text-primary' },
    locked: { label: 'Locked', icon: LockKeyhole, className: 'text-amber-500' },
  }[status]
  const StatusIcon = meta.icon

  return (
    <div className="group rounded-xl border border-border/55 bg-card/60 p-3.5 transition-colors hover:border-primary/20 hover:bg-card/80">
      <div className="flex items-center justify-between gap-3">
        <div className="flex h-8 w-8 items-center justify-center rounded-lg border border-border bg-primary/5 text-primary">
          <Icon className="h-3.5 w-3.5" />
        </div>
        <span
          className={cn('flex items-center gap-1 font-mono text-[7px] uppercase', meta.className)}
        >
          <StatusIcon className="h-2.5 w-2.5" />
          {meta.label}
        </span>
      </div>
      <h3 className="mt-2.5 text-xs font-semibold">{title}</h3>
      <p className="mt-1 text-[10px] leading-4 text-muted-foreground">{description}</p>
    </div>
  )
}

export function documentItem(document: DocumentRead): OverviewItem {
  const status = {
    pending: { label: 'pending', tone: 'neutral' as const },
    queued: { label: 'queued', tone: 'primary' as const },
    processing: { label: 'indexing', tone: 'primary' as const },
    indexed: { label: 'indexed', tone: 'success' as const },
    failed: { label: 'failed', tone: 'warning' as const },
  }[document.status]

  return {
    id: document.id,
    title: document.filename,
    detail: `${formatBytes(document.size_bytes)} · ${formatDate(document.updated_at)}`,
    status: status.label,
    tone: status.tone,
  }
}

export function workspaceItem(workspace: WorkspaceRead): OverviewItem {
  const knownState = {
    running: { label: 'target run', tone: 'primary' as const },
    paused: { label: 'paused', tone: 'warning' as const },
    stopped: { label: 'stopped', tone: 'neutral' as const },
    archived: { label: 'archived', tone: 'neutral' as const },
  }[workspace.desired_state]

  return {
    id: workspace.id,
    title: workspace.display_name,
    detail: `Observed ${workspace.observed_state || 'unknown'} · Gen ${workspace.generation}`,
    status: knownState.label,
    tone: knownState.tone,
  }
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

function formatDate(value: string): string {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '时间未知'
  return new Intl.DateTimeFormat('zh-CN', { month: 'short', day: 'numeric' }).format(date)
}
