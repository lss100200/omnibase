'use client'

import { useEffect, useMemo, useState } from 'react'
import Link from 'next/link'
import useSWR from 'swr'
import {
  Bot,
  Boxes,
  ChevronRight,
  CircleDot,
  Database,
  FileText,
  Folder,
  Gem,
  GitBranch,
  HardDrive,
  Plug,
  Plus,
  ShieldCheck,
  Store,
  Wrench,
} from 'lucide-react'
import { AIConversationWorkbench } from '@/components/ai/ai-conversation-workbench'
import { databaseApi, documentsApi, healthApi, workspacesApi } from '@/lib/api'
import { cn } from '@/lib/utils'

export default function DashboardPage() {
  const [selectedWorkspaceId, setSelectedWorkspaceId] = useState('')
  const {
    data: workspaces,
    error: workspacesError,
    isLoading: workspacesLoading,
  } = useSWR('workspaces-dashboard', () => workspacesApi.list())
  const {
    data: documents,
    error: documentsError,
    isLoading: documentsLoading,
  } = useSWR('documents-dashboard', () => documentsApi.list({ limit: 6 }))
  const {
    data: tables,
    error: tablesError,
    isLoading: tablesLoading,
  } = useSWR('database-dashboard', () => databaseApi.listTables())
  const { data: health, error: healthError } = useSWR('health', () => healthApi.readiness(), {
    refreshInterval: 30_000,
  })

  useEffect(() => {
    if (!selectedWorkspaceId && workspaces?.items[0]) {
      setSelectedWorkspaceId(workspaces.items[0].id)
    }
  }, [selectedWorkspaceId, workspaces])

  const currentWorkspace = useMemo(
    () => workspaces?.items.find((workspace) => workspace.id === selectedWorkspaceId),
    [selectedWorkspaceId, workspaces],
  )

  const browserApiOnline = !healthError && health?.status === 'ok'
  const contextLabel = currentWorkspace
    ? `${currentWorkspace.display_name} · 当前租户知识库`
    : '当前租户知识库 · 未选择 Workspace'

  return (
    <div className="production-workbench fade-up flex h-[calc(100vh-6rem)] min-h-[36rem] flex-col gap-3">
      <section
        className="flex shrink-0 flex-wrap items-center gap-2 rounded-xl border border-border/75 bg-card/75 px-2.5 py-2 shadow-[0_18px_50px_-42px_rgba(15,23,42,.9)]"
        aria-label="当前工作上下文"
      >
        <WorkspaceSelector
          value={selectedWorkspaceId}
          onChange={setSelectedWorkspaceId}
          items={workspaces?.items ?? []}
          loading={workspacesLoading}
          error={Boolean(workspacesError)}
        />
        <ContextField icon={Folder} label="Project" value="未连接" state="Preview" />
        <ContextField icon={GitBranch} label="Branch" value="未选择" state="Preview" />
        <ContextField
          icon={FileText}
          label="Context files"
          value={documentsError ? '未知' : documentsLoading ? '读取中' : `${documents?.total ?? 0}`}
        />
        <div className="ml-auto flex items-center gap-2">
          <span
            className={cn(
              'hidden items-center gap-1.5 rounded-lg px-2 py-1.5 font-mono text-[8px] uppercase sm:flex',
              browserApiOnline
                ? 'bg-emerald-500/8 text-emerald-500'
                : 'bg-amber-500/8 text-amber-500',
            )}
          >
            <CircleDot className="h-3 w-3" />
            API {browserApiOnline ? 'online' : 'unknown'}
          </span>
          <Link
            href="/spaces"
            className="flex h-9 items-center gap-2 rounded-lg bg-primary px-3 text-[10px] font-semibold text-primary-foreground transition-colors hover:bg-primary/90"
          >
            <Plus className="h-3.5 w-3.5" />
            新建 Workspace
          </Link>
        </div>
      </section>

      <div className="grid min-h-0 flex-1 gap-3 overflow-hidden xl:grid-cols-[minmax(0,1fr)_21rem]">
        <AIConversationWorkbench className="h-full" contextLabel={contextLabel} embedded />

        <aside className="flex min-h-0 flex-col overflow-hidden rounded-2xl border border-border/80 bg-card/75 shadow-[0_24px_70px_-55px_rgba(15,23,42,.85)]">
          <header className="flex h-14 shrink-0 items-center justify-between border-b border-border/75 px-4">
            <div>
              <p className="font-mono text-[7px] uppercase tracking-[0.18em] text-muted-foreground">
                Work context
              </p>
              <h2 className="mt-0.5 text-xs font-semibold">项目与能力侧栏</h2>
            </div>
            <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-primary/8 text-primary">
              <HardDrive className="h-4 w-4" />
            </span>
          </header>

          <div className="min-h-0 flex-1 overflow-y-auto p-3">
            <RailSection title="当前 Workspace" actionHref="/spaces" actionLabel="管理">
              {currentWorkspace ? (
                <Link
                  href={`/spaces/${currentWorkspace.id}`}
                  className="group flex items-center gap-3 rounded-xl border border-border/65 bg-background/35 p-3 transition-colors hover:border-primary/25 hover:bg-primary/[0.035]"
                >
                  <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-cyan-500/10 text-cyan-500">
                    <Boxes className="h-4 w-4" />
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-[11px] font-semibold">
                      {currentWorkspace.display_name}
                    </span>
                    <span className="mt-1 block truncate font-mono text-[8px] uppercase text-muted-foreground">
                      {currentWorkspace.desired_state} / {currentWorkspace.observed_state || 'unknown'}
                    </span>
                  </span>
                  <ChevronRight className="h-3.5 w-3.5 text-muted-foreground group-hover:text-primary" />
                </Link>
              ) : (
                <EmptyRailState
                  icon={Boxes}
                  title={workspacesError ? 'Workspace 接口未连接' : '尚未创建 Workspace'}
                  description={workspacesError ? '数据状态未知' : '创建一个受控工作空间开始组织项目'}
                  href="/spaces"
                  action="打开 Workspace"
                />
              )}

              <div className="mt-2 grid grid-cols-2 gap-2">
                <PreviewSlot icon={Folder} label="Project" value="未连接" />
                <PreviewSlot icon={GitBranch} label="Branch" value="未选择" />
              </div>
            </RailSection>

            <RailSection title="文件与知识" actionHref="/knowledge" actionLabel="全部文件">
              {documentsLoading ? (
                <p className="rounded-xl border border-dashed border-border/60 p-3 text-[9px] text-muted-foreground">
                  正在读取知识文件…
                </p>
              ) : documentsError ? (
                <p className="rounded-xl border border-dashed border-border/60 p-3 text-[9px] text-muted-foreground">
                  文件接口未连接，状态未知。
                </p>
              ) : documents?.items.length ? (
                <div className="space-y-1">
                  {documents.items.slice(0, 4).map((document) => (
                    <Link
                      key={document.id}
                      href="/knowledge"
                      className="flex items-center gap-2 rounded-lg px-2 py-2 text-[10px] transition-colors hover:bg-muted/45"
                    >
                      <FileText className="h-3.5 w-3.5 shrink-0 text-primary" />
                      <span className="min-w-0 flex-1 truncate">{document.filename}</span>
                      <span
                        className={cn(
                          'font-mono text-[7px] uppercase',
                          document.status === 'indexed'
                            ? 'text-emerald-500'
                            : document.status === 'failed'
                              ? 'text-amber-500'
                              : 'text-muted-foreground',
                        )}
                      >
                        {document.status}
                      </span>
                    </Link>
                  ))}
                </div>
              ) : (
                <EmptyRailState
                  icon={FileText}
                  title="暂无知识文件"
                  description="上传文档后，它们会作为可追溯的 AI 上下文显示在这里。"
                  href="/knowledge"
                  action="添加文件"
                />
              )}
              <div className="mt-2 flex items-center justify-between rounded-lg border border-dashed border-border/60 px-3 py-2 text-[9px] text-muted-foreground">
                <span className="flex items-center gap-2">
                  <Folder className="h-3.5 w-3.5" />
                  代码项目文件树
                </span>
                <PreviewBadge label="Preview" />
              </div>
            </RailSection>

            <RailSection title="数据工作台" actionHref="/database" actionLabel="打开数据库">
              <Link
                href="/database"
                className="group flex items-center gap-3 rounded-xl border border-emerald-400/15 bg-emerald-500/[0.035] p-3 transition-colors hover:border-emerald-400/30 hover:bg-emerald-500/[0.06]"
              >
                <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-emerald-500/10 text-emerald-500">
                  <Database className="h-4 w-4" />
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block text-[11px] font-semibold">逻辑数据库浏览</span>
                  <span className="mt-1 block text-[8px] text-muted-foreground">
                    {tablesError
                      ? '接口未连接 · 状态未知'
                      : tablesLoading
                        ? '正在读取表结构…'
                        : `${tables?.tables.length ?? 0} 个可见逻辑表`}
                  </span>
                </span>
                <ChevronRight className="h-3.5 w-3.5 text-emerald-500" />
              </Link>
            </RailSection>

            <RailSection title="扩展你的人工智能">
              <div className="grid grid-cols-2 gap-2">
                <ExtensionEntry icon={Bot} title="AI 员工" state="Preview" tone="primary" />
                <ExtensionEntry icon={Wrench} title="Skills" state="Preview" tone="primary" />
                <ExtensionEntry icon={Plug} title="MCP" state="Locked" tone="warning" />
                <ExtensionEntry icon={Store} title="市场" state="Preview" tone="primary" />
              </div>
              <div className="mt-2 rounded-xl border border-indigo-400/15 bg-indigo-500/[0.035] p-3">
                <div className="flex items-center gap-2">
                  <Gem className="h-3.5 w-3.5 text-primary" />
                  <span className="text-[10px] font-semibold">创建自己的 AI 员工</span>
                  <PreviewBadge label="Preview" className="ml-auto" />
                </div>
                <p className="mt-1.5 text-[8px] leading-4 text-muted-foreground">
                  定义角色、技能、知识与预算。Agent Runtime 尚未解锁，不会自动执行。
                </p>
              </div>
            </RailSection>
          </div>

          <footer className="flex shrink-0 items-center gap-2 border-t border-border/75 px-4 py-3 text-[8px] text-muted-foreground">
            <ShieldCheck className="h-3.5 w-3.5 text-amber-500" />
            Runtime locked · P34.7 / Phase 5 production not proven
          </footer>
        </aside>
      </div>
    </div>
  )
}

function WorkspaceSelector({
  value,
  onChange,
  items,
  loading,
  error,
}: {
  value: string
  onChange: (value: string) => void
  items: Array<{ id: string; display_name: string }>
  loading: boolean
  error: boolean
}) {
  return (
    <label className="flex h-10 min-w-[13rem] items-center gap-2 rounded-lg border border-border/70 bg-background/40 px-2.5">
      <Boxes className="h-3.5 w-3.5 shrink-0 text-primary" />
      <span className="font-mono text-[7px] uppercase tracking-wider text-muted-foreground">
        Workspace
      </span>
      <select
        value={value}
        onChange={(event) => onChange(event.target.value)}
        disabled={loading || error || items.length === 0}
        className="min-w-0 flex-1 bg-transparent text-[10px] font-medium outline-none disabled:text-muted-foreground"
        aria-label="选择 Workspace"
      >
        {loading && <option value="">读取中</option>}
        {error && <option value="">状态未知</option>}
        {!loading && !error && items.length === 0 && <option value="">未创建</option>}
        {items.map((workspace) => (
          <option key={workspace.id} value={workspace.id}>
            {workspace.display_name}
          </option>
        ))}
      </select>
    </label>
  )
}

function ContextField({
  icon: Icon,
  label,
  value,
  state,
}: {
  icon: React.ComponentType<{ className?: string }>
  label: string
  value: string
  state?: 'Preview'
}) {
  return (
    <div className="flex h-10 items-center gap-2 rounded-lg border border-border/70 bg-background/40 px-2.5">
      <Icon className="h-3.5 w-3.5 text-muted-foreground" />
      <span className="font-mono text-[7px] uppercase tracking-wider text-muted-foreground">
        {label}
      </span>
      <span className="max-w-24 truncate text-[10px] font-medium">{value}</span>
      {state && <PreviewBadge label={state} />}
    </div>
  )
}

function RailSection({
  title,
  actionHref,
  actionLabel,
  children,
}: {
  title: string
  actionHref?: string
  actionLabel?: string
  children: React.ReactNode
}) {
  return (
    <section className="border-b border-border/70 py-3 first:pt-0 last:border-b-0 last:pb-0">
      <div className="mb-2.5 flex items-center justify-between gap-3 px-1">
        <h3 className="text-[10px] font-semibold">{title}</h3>
        {actionHref && actionLabel && (
          <Link href={actionHref} className="text-[8px] text-primary hover:underline">
            {actionLabel}
          </Link>
        )}
      </div>
      {children}
    </section>
  )
}

function PreviewSlot({
  icon: Icon,
  label,
  value,
}: {
  icon: React.ComponentType<{ className?: string }>
  label: string
  value: string
}) {
  return (
    <div className="rounded-lg border border-dashed border-border/60 bg-muted/10 p-2.5">
      <div className="flex items-center gap-1.5 text-[8px] text-muted-foreground">
        <Icon className="h-3 w-3" />
        {label}
      </div>
      <div className="mt-1.5 flex items-center justify-between gap-2">
        <span className="truncate text-[9px]">{value}</span>
        <PreviewBadge label="Preview" />
      </div>
    </div>
  )
}

function ExtensionEntry({
  icon: Icon,
  title,
  state,
  tone,
}: {
  icon: React.ComponentType<{ className?: string }>
  title: string
  state: string
  tone: 'primary' | 'warning'
}) {
  return (
    <div className="rounded-xl border border-border/65 bg-background/30 p-2.5">
      <div className="flex items-center justify-between gap-2">
        <Icon className={cn('h-3.5 w-3.5', tone === 'primary' ? 'text-primary' : 'text-amber-500')} />
        <span
          className={cn(
            'font-mono text-[6px] uppercase',
            tone === 'primary' ? 'text-primary' : 'text-amber-500',
          )}
        >
          {state}
        </span>
      </div>
      <p className="mt-2 text-[9px] font-medium">{title}</p>
    </div>
  )
}

function EmptyRailState({
  icon: Icon,
  title,
  description,
  href,
  action,
}: {
  icon: React.ComponentType<{ className?: string }>
  title: string
  description: string
  href: string
  action: string
}) {
  return (
    <div className="rounded-xl border border-dashed border-border/65 bg-muted/10 p-3 text-center">
      <Icon className="mx-auto h-4 w-4 text-muted-foreground" />
      <p className="mt-2 text-[10px] font-medium">{title}</p>
      <p className="mt-1 text-[8px] leading-4 text-muted-foreground">{description}</p>
      <Link href={href} className="mt-2 inline-flex items-center gap-1 text-[8px] text-primary hover:underline">
        {action}
        <ChevronRight className="h-3 w-3" />
      </Link>
    </div>
  )
}

function PreviewBadge({
  label,
  className,
}: {
  label: string
  className?: string
}) {
  return (
    <span
      className={cn(
        'shrink-0 rounded-full bg-primary/10 px-1.5 py-0.5 font-mono text-[6px] uppercase tracking-wider text-primary',
        className,
      )}
    >
      {label}
    </span>
  )
}
