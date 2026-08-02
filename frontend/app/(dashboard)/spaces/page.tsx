'use client'

import { useState } from 'react'
import Link from 'next/link'
import useSWR from 'swr'
import { Boxes, Loader2, Plus, ShieldCheck } from 'lucide-react'
import { workspacesApi, getApiErrorMessage } from '@/lib/api'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogTrigger } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'

export default function SpacesPage() {
  const { data, error, isLoading, mutate } = useSWR('workspaces', () => workspacesApi.list())
  const { data: templates } = useSWR('workspace-templates', () => workspacesApi.listTemplates())
  const [open, setOpen] = useState(false)
  const [name, setName] = useState('')
  const [templateId, setTemplateId] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [formError, setFormError] = useState<string | null>(null)

  async function createSpace() {
    if (!name.trim() || !templateId) return
    setSubmitting(true)
    setFormError(null)
    try {
      await workspacesApi.create({ display_name: name.trim(), template_id: templateId })
      await mutate()
      setName('')
      setTemplateId('')
      setOpen(false)
    } catch (createError) {
      setFormError(getApiErrorMessage(createError, '无法创建 AI 空间'))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">AI 空间</h1>
          <p className="mt-1 max-w-3xl text-sm text-muted-foreground">
            长期 Workspace 与短期 Run 分离。浏览器只管理控制面；Sandbox、私有数据和 RAG
            能力必须通过独立 Gateway、Lease、fencing、预算与审计。
          </p>
        </div>
        <Dialog open={open} onOpenChange={setOpen}>
          <DialogTrigger asChild>
            <Button><Plus className="mr-2 h-4 w-4" />创建空间</Button>
          </DialogTrigger>
          <DialogContent>
            <DialogHeader><DialogTitle>从受控模板创建 AI 空间</DialogTitle></DialogHeader>
            <div className="space-y-4">
              <div className="space-y-2">
                <Label htmlFor="space-name">名称</Label>
                <Input id="space-name" value={name} onChange={(event) => setName(event.target.value)} />
              </div>
              <div className="space-y-2">
                <Label>模板版本</Label>
                <Select value={templateId} onValueChange={setTemplateId}>
                  <SelectTrigger><SelectValue placeholder="选择已注册模板" /></SelectTrigger>
                  <SelectContent>
                    {templates?.items.map((template) => (
                      <SelectItem key={template.id} value={template.id}>
                        {template.display_name} · v{template.version}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              {formError && <p className="text-sm text-destructive">{formError}</p>}
              <Button className="w-full" disabled={submitting || !name.trim() || !templateId} onClick={createSpace}>
                {submitting && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}创建
              </Button>
            </div>
          </DialogContent>
        </Dialog>
      </div>

      <Card className="border-primary/20 bg-primary/5">
        <CardContent className="flex gap-3 pt-6 text-sm">
          <ShieldCheck className="mt-0.5 h-5 w-5 shrink-0 text-primary" />
          <p>
            P34.7 生产总 Gate 尚未完成，因此 Agent Runtime 保持关闭。这里开放的是经过租户与成员实时复核的
            Workspace 控制面，不代表浏览器获得 Sandbox 或数据库写权限。
          </p>
        </CardContent>
      </Card>

      {isLoading && <p className="text-sm text-muted-foreground">正在加载空间…</p>}
      {error && <p className="text-sm text-destructive">{getApiErrorMessage(error, '无法加载空间')}</p>}
      {!isLoading && !error && data?.items.length === 0 && (
        <Card><CardContent className="flex flex-col items-center py-14 text-center">
          <Boxes className="mb-4 h-10 w-10 text-muted-foreground" />
          <p className="font-medium">还没有 AI 空间</p>
          <p className="mt-1 text-sm text-muted-foreground">先注册或选择一个版本化模板，再创建 Workspace。</p>
        </CardContent></Card>
      )}

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        {data?.items.map((workspace) => (
          <Link href={`/spaces/${workspace.id}`} key={workspace.id}>
            <Card className="h-full transition-colors hover:bg-accent/40">
              <CardHeader>
                <div className="flex items-start justify-between gap-3">
                  <CardTitle className="text-lg">{workspace.display_name}</CardTitle>
                  <Badge variant={workspace.observed_state === 'running' ? 'success' : 'secondary'}>
                    {workspace.observed_state}
                  </Badge>
                </div>
                <CardDescription>generation {workspace.generation} · version {workspace.version}</CardDescription>
              </CardHeader>
              <CardContent className="text-xs text-muted-foreground">
                更新于 {new Date(workspace.updated_at).toLocaleString()}
              </CardContent>
            </Card>
          </Link>
        ))}
      </div>
    </div>
  )
}
