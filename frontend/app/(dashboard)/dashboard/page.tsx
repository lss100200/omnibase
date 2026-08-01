'use client'

import Link from 'next/link'
import useSWR from 'swr'
import { BookOpen, Database, FileText, Sparkles } from 'lucide-react'
import { documentsApi, healthApi } from '@/lib/api'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { useAuth } from '@/lib/hooks/use-auth'

export default function DashboardPage() {
  const { user, tenant } = useAuth()

  // Document count
  const { data: docsData, isLoading: docsLoading } = useSWR('documents-summary', () =>
    documentsApi.list({ limit: 1 }),
  )

  // Backend readiness
  const { data: health } = useSWR('health', () => healthApi.readiness().catch(() => null), {
    refreshInterval: 30_000,
  })

  return (
    <div className="space-y-6">
      {/* Greeting */}
      <div>
        <h1 className="text-2xl font-bold tracking-tight">
          欢迎，{user?.email.split('@')[0] || '探索者'} 👋
        </h1>
        <p className="mt-1 text-sm text-muted-foreground">
          这是你的专属知识工作台「{tenant?.name || '默认空间'}」。开始上传文档，让 AI 帮你管理知识。
        </p>
      </div>

      {/* Quick stats */}
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard
          title="文档总数"
          icon={FileText}
          value={docsLoading ? null : (docsData?.total ?? 0)}
          href="/knowledge"
        />
        <StatCard title="工作空间" icon={BookOpen} value={tenant?.name || '—'} href="/knowledge" />
        <StatCard
          title="数据库"
          icon={Database}
          value="在线"
          href="/database"
          status={
            health?.status === 'ok'
              ? 'success'
              : health?.status === 'degraded'
                ? 'warning'
                : 'destructive'
          }
        />
        <StatCard title="RAG 引擎" icon={Sparkles} value="即将上线" status="warning" />
      </div>

      {/* Quick start */}
      <Card>
        <CardHeader>
          <CardTitle className="text-lg">快速开始</CardTitle>
          <CardDescription>三个步骤开始使用 OmniBase</CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <Step n={1} title="上传你的第一份文档">
            支持 PDF / DOCX / TXT / Markdown，自动提取页数与元数据。
          </Step>
          <Step n={2} title="浏览数据库">
            直接查看后台数据结构，体验 schema-per-tenant 隔离。
          </Step>
          <Step n={3} title="等待 RAG 上线">
            Phase 1 将带来向量检索、智能问答和 Agent 编排。
          </Step>
          <div className="flex gap-2 pt-2">
            <Button asChild>
              <Link href="/knowledge">前往知识库</Link>
            </Button>
            <Button variant="outline" asChild>
              <Link href="/database">查看数据库</Link>
            </Button>
          </div>
        </CardContent>
      </Card>
    </div>
  )
}

function StatCard({
  title,
  icon: Icon,
  value,
  href,
  status,
}: {
  title: string
  icon: React.ComponentType<{ className?: string }>
  value: number | string | null
  href?: string
  status?: 'success' | 'warning' | 'destructive'
}) {
  const content = (
    <Card className="transition-colors hover:bg-accent/40">
      <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
        <CardTitle className="text-sm font-medium text-muted-foreground">{title}</CardTitle>
        <Icon className="h-4 w-4 text-muted-foreground" />
      </CardHeader>
      <CardContent>
        {value === null ? (
          <Skeleton className="h-7 w-16" />
        ) : (
          <div className="flex items-center gap-2">
            <span className="text-2xl font-bold">{value}</span>
            {status && <Badge variant={status} className="h-2 w-2 rounded-full p-0" />}
          </div>
        )}
      </CardContent>
    </Card>
  )

  return href ? <Link href={href}>{content}</Link> : content
}

function Step({ n, title, children }: { n: number; title: string; children: React.ReactNode }) {
  return (
    <div className="flex gap-3">
      <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-primary text-xs font-bold text-primary-foreground">
        {n}
      </div>
      <div>
        <p className="text-sm font-medium">{title}</p>
        <p className="text-sm text-muted-foreground">{children}</p>
      </div>
    </div>
  )
}
