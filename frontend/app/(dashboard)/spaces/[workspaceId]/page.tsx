'use client'

import { useState } from 'react'
import { useParams } from 'next/navigation'
import useSWR from 'swr'
import { Activity, Archive, Database, FileClock, Pause, Play, Shield, Square } from 'lucide-react'
import { getApiErrorMessage, workspacesApi } from '@/lib/api'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'

export default function SpaceDetailPage() {
  const params = useParams<{ workspaceId: string }>()
  const workspaceId = params.workspaceId
  const { data: workspace, error, mutate } = useSWR(['workspace', workspaceId], () => workspacesApi.get(workspaceId))
  const { data: members } = useSWR(['workspace-members', workspaceId], () => workspacesApi.listMembers(workspaceId))
  const { data: runs, mutate: mutateRuns } = useSWR(['workspace-runs', workspaceId], () => workspacesApi.listRuns(workspaceId))
  const [actionError, setActionError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  async function transition(action: 'start' | 'pause' | 'stop' | 'archive') {
    if (!workspace) return
    setBusy(true)
    setActionError(null)
    try {
      await workspacesApi.requestState(workspaceId, action, workspace.version)
      await mutate()
    } catch (transitionError) {
      setActionError(getApiErrorMessage(transitionError, '生命周期操作被拒绝'))
    } finally {
      setBusy(false)
    }
  }

  async function createRun() {
    if (!workspace) return
    setBusy(true)
    setActionError(null)
    try {
      await workspacesApi.createRun(workspaceId, workspace.generation, 'interactive')
      await mutateRuns()
    } catch (runError) {
      setActionError(getApiErrorMessage(runError, 'Run 创建被拒绝'))
    } finally {
      setBusy(false)
    }
  }

  if (error) return <p className="text-sm text-destructive">{getApiErrorMessage(error, '无法加载空间')}</p>
  if (!workspace) return <p className="text-sm text-muted-foreground">正在加载空间…</p>

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-4 xl:flex-row xl:items-start xl:justify-between">
        <div>
          <div className="flex items-center gap-3">
            <h1 className="text-2xl font-bold tracking-tight">{workspace.display_name}</h1>
            <Badge variant={workspace.observed_state === 'running' ? 'success' : 'secondary'}>{workspace.observed_state}</Badge>
          </div>
          <p className="mt-1 text-sm text-muted-foreground">
            Workspace {workspace.id} · generation {workspace.generation} · fencing-sensitive control plane
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button size="sm" disabled={busy} onClick={() => transition('start')}><Play className="mr-2 h-4 w-4" />启动</Button>
          <Button size="sm" variant="outline" disabled={busy} onClick={() => transition('pause')}><Pause className="mr-2 h-4 w-4" />暂停</Button>
          <Button size="sm" variant="outline" disabled={busy} onClick={() => transition('stop')}><Square className="mr-2 h-4 w-4" />停止</Button>
          <Button size="sm" variant="destructive" disabled={busy} onClick={() => transition('archive')}><Archive className="mr-2 h-4 w-4" />归档</Button>
        </div>
      </div>
      {actionError && <p className="text-sm text-destructive">{actionError}</p>}

      <Tabs defaultValue="overview">
        <TabsList className="flex h-auto flex-wrap justify-start">
          <TabsTrigger value="overview">概览</TabsTrigger>
          <TabsTrigger value="runs">Run / Session</TabsTrigger>
          <TabsTrigger value="members">成员</TabsTrigger>
          <TabsTrigger value="data">数据与能力</TabsTrigger>
          <TabsTrigger value="snapshots">快照与恢复</TabsTrigger>
          <TabsTrigger value="logs">日志与审计</TabsTrigger>
        </TabsList>

        <TabsContent value="overview" className="grid gap-4 md:grid-cols-3">
          <Metric title="期望状态" value={workspace.desired_state} icon={Activity} />
          <Metric title="当前代次" value={workspace.generation} icon={FileClock} />
          <Metric title="成员" value={members?.total ?? '—'} icon={Shield} />
        </TabsContent>

        <TabsContent value="runs" className="space-y-4">
          <div className="flex justify-end"><Button disabled={busy} onClick={createRun}>创建受控 Run</Button></div>
          {runs?.items.map((run) => (
            <Card key={run.id}><CardHeader><CardTitle className="text-base">{run.kind} · {run.id}</CardTitle>
              <CardDescription>{run.observed_state} · generation {run.generation}</CardDescription></CardHeader></Card>
          ))}
          {runs?.items.length === 0 && <p className="text-sm text-muted-foreground">暂无 Run。P34.7 完成前，Run 不会自动启动 Agent。</p>}
        </TabsContent>

        <TabsContent value="members" className="space-y-3">
          {members?.items.map((member) => (
            <Card key={member.id}><CardContent className="flex items-center justify-between py-4 text-sm">
              <span>{member.user_id}</span><div className="flex gap-2"><Badge variant="outline">{member.role}</Badge><Badge variant="secondary">{member.state}</Badge></div>
            </CardContent></Card>
          ))}
        </TabsContent>

        <TabsContent value="data">
          <Card><CardHeader><CardTitle className="flex items-center gap-2 text-base"><Database className="h-4 w-4" />Gateway-only 数据边界</CardTitle>
            <CardDescription>浏览器不接收 WorkspaceData 写 token、物理 locator、数据库连接或对象存储凭据。</CardDescription></CardHeader>
            <CardContent className="text-sm text-muted-foreground">Artifact、Derived RAG、Promotion 与恢复只能由受信 workload 通过独立 mTLS Capability Gateway 调用。P34.7 provider Gate 未通过时对应 adapter 保持 unavailable。</CardContent></Card>
        </TabsContent>

        <TabsContent value="snapshots">
          <Card><CardHeader><CardTitle className="text-base">快照与 restore-new-identity</CardTitle>
            <CardDescription>授权、token、Run、Lease、runtime/workload identity 不进入快照。</CardDescription></CardHeader>
            <CardContent className="space-y-3 text-sm text-muted-foreground">
              <p>浏览器不能提交自称可信的 manifest digest。P34.7 provider-backed snapshot barrier 与服务端 inventory Gate 通过后，才会开放正式快照入口。</p>
              <Button disabled>等待 P34.7 快照 Gate</Button>
            </CardContent></Card>
        </TabsContent>

        <TabsContent value="logs">
          <Card><CardHeader><CardTitle className="text-base">日志与审计边界</CardTitle></CardHeader>
            <CardContent className="text-sm text-muted-foreground">运行日志、Gateway Audit、安全拒绝和 reconciliation 将按 request/run/workspace/operation ID 关联；敏感字段与物理 locator 必须在服务端脱敏。当前浏览器尚未开放原始日志流。</CardContent></Card>
        </TabsContent>
      </Tabs>
    </div>
  )
}

function Metric({ title, value, icon: Icon }: { title: string; value: string | number; icon: React.ComponentType<{ className?: string }> }) {
  return <Card><CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2"><CardTitle className="text-sm font-medium text-muted-foreground">{title}</CardTitle><Icon className="h-4 w-4 text-muted-foreground" /></CardHeader><CardContent><p className="text-2xl font-bold">{value}</p></CardContent></Card>
}
