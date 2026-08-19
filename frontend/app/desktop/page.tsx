'use client'

import { FormEvent, useCallback, useEffect, useRef, useState } from 'react'
import {
  Archive,
  Bot,
  Boxes,
  Database,
  HardDrive,
  Loader2,
  Plus,
  RefreshCw,
  ShieldCheck,
  UserRound,
} from 'lucide-react'
import {
  DesktopOwner,
  DesktopWorkspace,
  getDesktopBridge,
  type OmniBaseDesktopBridge,
} from '@/lib/desktop-bridge'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Separator } from '@/components/ui/separator'

type LoadState = 'loading' | 'ready' | 'unsupported' | 'failed'

const ERROR_MESSAGES: Readonly<Record<string, string>> = {
  desktop_native_input_invalid: '输入不符合本机控制边界。',
  desktop_native_request_failed: '本机服务暂时不可用。',
  desktop_native_response_invalid: '本机服务返回了无法验证的数据。',
  desktop_owner_not_initialized: '请先建立本机 Owner。',
  desktop_runtime_not_ready: '本机运行时尚未就绪。',
  desktop_workspace_not_found: '工作空间不存在或已被移除。',
  desktop_workspace_version_conflict: '工作空间已发生变化，请刷新后重试。',
  desktop_workspace_capacity_reached: '本机工作空间数量已达到上限。',
}

function errorMessage(code: string): string {
  return ERROR_MESSAGES[code] ?? '操作未完成；本机服务已安全拒绝该请求。'
}

function formatDate(value: string): string {
  const date = new Date(value)
  return Number.isNaN(date.getTime())
    ? '时间不可用'
    : date.toLocaleString([], {
        year: 'numeric',
        month: '2-digit',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit',
      })
}

export default function DesktopAdmissionPage() {
  const bridgeRef = useRef<OmniBaseDesktopBridge | null>(null)
  const [loadState, setLoadState] = useState<LoadState>('loading')
  const [version, setVersion] = useState('1.0.0')
  const [owner, setOwner] = useState<DesktopOwner | null>(null)
  const [workspaces, setWorkspaces] = useState<readonly DesktopWorkspace[]>([])
  const [displayName, setDisplayName] = useState('')
  const [workspaceName, setWorkspaceName] = useState('我的工作空间')
  const [newWorkspaceName, setNewWorkspaceName] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [operationError, setOperationError] = useState<string | null>(null)
  const [archiveTarget, setArchiveTarget] = useState<DesktopWorkspace | null>(null)

  const load = useCallback(async () => {
    setLoadState('loading')
    setOperationError(null)
    const bridge = getDesktopBridge()
    bridgeRef.current = bridge
    if (bridge === null) {
      setLoadState('unsupported')
      return
    }
    try {
      const [resolvedVersion, ownerResult] = await Promise.all([
        bridge.app.getVersion(),
        bridge.owner.getStatus(),
      ])
      if (!ownerResult.ok) {
        setOperationError(errorMessage(ownerResult.error.code))
        setLoadState('failed')
        return
      }
      setVersion(resolvedVersion)
      setOwner(ownerResult.value.owner)
      if (ownerResult.value.initialized) {
        const workspaceResult = await bridge.workspaces.list()
        if (!workspaceResult.ok) {
          setOperationError(errorMessage(workspaceResult.error.code))
          setLoadState('failed')
          return
        }
        setWorkspaces(workspaceResult.value.items)
      } else {
        setWorkspaces([])
      }
      setLoadState('ready')
    } catch {
      setOperationError('无法连接到受信的 OmniBase 桌面桥。')
      setLoadState('failed')
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  const bootstrapOwner = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const bridge = bridgeRef.current
    const normalizedOwner = displayName.trim()
    const normalizedWorkspace = workspaceName.trim()
    if (bridge === null || normalizedOwner === '' || normalizedWorkspace === '') return

    setSubmitting(true)
    setOperationError(null)
    try {
      const ownerResult = await bridge.owner.bootstrap({ displayName: normalizedOwner })
      if (!ownerResult.ok) {
        setOperationError(errorMessage(ownerResult.error.code))
        return
      }
      setOwner(ownerResult.value.owner)
      if (ownerResult.value.created) {
        const workspaceResult = await bridge.workspaces.create({ name: normalizedWorkspace })
        if (!workspaceResult.ok) {
          setOperationError(errorMessage(workspaceResult.error.code))
          return
        }
        setWorkspaces([workspaceResult.value.workspace])
      } else {
        const workspaceResult = await bridge.workspaces.list()
        if (!workspaceResult.ok) {
          setOperationError(errorMessage(workspaceResult.error.code))
          return
        }
        setWorkspaces(workspaceResult.value.items)
      }
    } catch {
      setOperationError('本机 Owner 初始化未完成。')
    } finally {
      setSubmitting(false)
    }
  }

  const createWorkspace = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const bridge = bridgeRef.current
    const normalized = newWorkspaceName.trim()
    if (bridge === null || normalized === '') return

    setSubmitting(true)
    setOperationError(null)
    try {
      const result = await bridge.workspaces.create({ name: normalized })
      if (!result.ok) {
        setOperationError(errorMessage(result.error.code))
        return
      }
      setWorkspaces((current) => [
        ...current.filter((workspace) => workspace.id !== result.value.workspace.id),
        result.value.workspace,
      ])
      setNewWorkspaceName('')
    } catch {
      setOperationError('工作空间创建未完成。')
    } finally {
      setSubmitting(false)
    }
  }

  const archiveWorkspace = async () => {
    const bridge = bridgeRef.current
    const target = archiveTarget
    if (bridge === null || target === null) return

    setSubmitting(true)
    setOperationError(null)
    try {
      const result = await bridge.workspaces.archive({
        workspaceId: target.id,
        expectedRowVersion: target.rowVersion,
      })
      if (!result.ok) {
        setOperationError(errorMessage(result.error.code))
        return
      }
      setWorkspaces((current) =>
        current.map((workspace) =>
          workspace.id === result.value.workspace.id ? result.value.workspace : workspace,
        ),
      )
      setArchiveTarget(null)
    } catch {
      setOperationError('工作空间归档未完成。')
    } finally {
      setSubmitting(false)
    }
  }

  if (loadState === 'loading') {
    return (
      <main className="flex min-h-screen items-center justify-center bg-background p-8">
        <div className="flex items-center gap-3 text-sm text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" />
          正在验证本机 Owner 与 SQLite 状态…
        </div>
      </main>
    )
  }

  if (loadState === 'unsupported') {
    return (
      <main className="flex min-h-screen items-center justify-center bg-background p-8">
        <Card className="w-full max-w-xl rounded-none">
          <CardHeader>
            <CardTitle>仅可从 OmniBase 桌面应用访问</CardTitle>
            <CardDescription>
              此页面没有 HTTP 降级路径，也不会从浏览器读取本机控制凭据。
            </CardDescription>
          </CardHeader>
        </Card>
      </main>
    )
  }

  if (loadState === 'failed') {
    return (
      <main className="flex min-h-screen items-center justify-center bg-background p-8">
        <Card className="w-full max-w-xl rounded-none border-destructive/50">
          <CardHeader>
            <CardTitle>本机工作台未能安全打开</CardTitle>
            <CardDescription>{operationError}</CardDescription>
          </CardHeader>
          <CardContent>
            <Button type="button" variant="outline" onClick={() => void load()}>
              <RefreshCw className="h-4 w-4" />
              重新验证
            </Button>
          </CardContent>
        </Card>
      </main>
    )
  }

  if (owner === null) {
    return (
      <main className="min-h-screen bg-background px-5 py-10 sm:px-8">
        <div className="mx-auto grid w-full max-w-5xl gap-8 lg:grid-cols-[1.1fr_.9fr]">
          <section className="flex flex-col justify-between border border-border bg-card p-7 sm:p-10">
            <div>
              <Badge variant="outline" className="rounded-none font-mono uppercase">
                Desktop Local / P6.6 R0
              </Badge>
              <h1 className="mt-8 text-4xl font-semibold tracking-[-0.05em] sm:text-5xl">
                建立你的本机 OmniBase
              </h1>
              <p className="mt-5 max-w-xl text-base leading-7 text-muted-foreground">
                一个 Windows 用户只建立一个 Owner。数据写入本机 SQLite；不会要求邮箱、密码、
                Docker、WSL 或 PostgreSQL。
              </p>
            </div>
            <div className="mt-12 grid gap-3 text-sm sm:grid-cols-3">
              <div className="border border-border p-4">
                <HardDrive className="mb-3 h-5 w-5" />
                本机持久化
              </div>
              <div className="border border-border p-4">
                <ShieldCheck className="mb-3 h-5 w-5" />
                原生 IPC 写入
              </div>
              <div className="border border-border p-4">
                <Database className="mb-3 h-5 w-5" />
                SQLite 审计
              </div>
            </div>
          </section>

          <Card className="rounded-none">
            <CardHeader className="pb-5">
              <div className="font-mono text-xs uppercase tracking-[0.16em] text-muted-foreground">
                First-run admission
              </div>
              <CardTitle className="pt-2 text-2xl">初始化本机 Owner</CardTitle>
              <CardDescription>
                Owner 与第一个工作空间分别以事务写入；中断后可安全重试。
              </CardDescription>
            </CardHeader>
            <CardContent>
              <form className="space-y-5" onSubmit={bootstrapOwner}>
                <div className="space-y-2">
                  <Label htmlFor="desktop-owner-name">显示名称</Label>
                  <Input
                    id="desktop-owner-name"
                    value={displayName}
                    onChange={(event) => setDisplayName(event.target.value)}
                    maxLength={256}
                    autoFocus
                    placeholder="例如：我的 OmniBase"
                    className="h-11 rounded-none"
                  />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="desktop-first-workspace">第一个工作空间</Label>
                  <Input
                    id="desktop-first-workspace"
                    value={workspaceName}
                    onChange={(event) => setWorkspaceName(event.target.value)}
                    maxLength={256}
                    className="h-11 rounded-none"
                  />
                </div>
                {operationError && (
                  <p role="alert" className="text-sm text-destructive">
                    {operationError}
                  </p>
                )}
                <Button
                  type="submit"
                  className="h-11 w-full rounded-none"
                  disabled={submitting || displayName.trim() === '' || workspaceName.trim() === ''}
                >
                  {submitting ? (
                    <Loader2 className="h-4 w-4 animate-spin" />
                  ) : (
                    <UserRound className="h-4 w-4" />
                  )}
                  建立本机工作台
                </Button>
              </form>
              <Separator className="my-6" />
              <p className="text-xs leading-5 text-muted-foreground">
                OmniBase {version} · 未签名工程验收版本。此步骤不会启用 Agent、Provider
                或外部网络能力。
              </p>
            </CardContent>
          </Card>
        </div>
      </main>
    )
  }

  const activeWorkspaces = workspaces.filter((workspace) => workspace.state === 'active')
  const archivedWorkspaces = workspaces.filter((workspace) => workspace.state === 'archived')

  return (
    <main className="min-h-screen bg-background">
      <header className="border-b border-border bg-card/70">
        <div className="mx-auto flex max-w-6xl flex-col gap-4 px-5 py-6 sm:flex-row sm:items-center sm:justify-between sm:px-8">
          <div>
            <div className="font-mono text-xs uppercase tracking-[0.16em] text-muted-foreground">
              OmniBase {version} / Desktop Local
            </div>
            <h1 className="mt-2 text-2xl font-semibold tracking-tight">{owner.displayName}</h1>
          </div>
          <div className="flex items-center gap-2">
            <Badge variant="outline" className="rounded-none">
              <ShieldCheck className="mr-1 h-3.5 w-3.5" />
              原生控制已验证
            </Badge>
            <Button type="button" variant="outline" size="sm" onClick={() => void load()}>
              <RefreshCw className="h-4 w-4" />
              刷新
            </Button>
          </div>
        </div>
      </header>

      <div className="mx-auto grid max-w-6xl gap-6 px-5 py-7 sm:px-8 lg:grid-cols-[1fr_320px]">
        <section className="space-y-6">
          <Card className="rounded-none">
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <Boxes className="h-5 w-5" />
                本机工作空间
              </CardTitle>
              <CardDescription>
                创建与归档都使用 SQLite 事务、行版本检查和追加式审计事件。
              </CardDescription>
            </CardHeader>
            <CardContent>
              <form className="flex flex-col gap-3 sm:flex-row" onSubmit={createWorkspace}>
                <Input
                  aria-label="新工作空间名称"
                  value={newWorkspaceName}
                  onChange={(event) => setNewWorkspaceName(event.target.value)}
                  maxLength={256}
                  placeholder="新工作空间名称"
                  className="h-10 flex-1 rounded-none"
                />
                <Button
                  type="submit"
                  className="rounded-none"
                  disabled={submitting || newWorkspaceName.trim() === ''}
                >
                  <Plus className="h-4 w-4" />
                  创建
                </Button>
              </form>
              {operationError && (
                <p role="alert" className="mt-4 text-sm text-destructive">
                  {operationError}
                </p>
              )}
              <div className="mt-6 space-y-3">
                {activeWorkspaces.length === 0 && (
                  <div className="border border-dashed border-border p-6 text-sm text-muted-foreground">
                    暂无活动工作空间。上方创建操作不会启动任何 Agent Runtime。
                  </div>
                )}
                {activeWorkspaces.map((workspace) => (
                  <div
                    key={workspace.id}
                    className="flex flex-col gap-4 border border-border bg-background p-4 sm:flex-row sm:items-center sm:justify-between"
                  >
                    <div className="min-w-0">
                      <div className="truncate font-medium">{workspace.name}</div>
                      <div className="mt-1 font-mono text-xs text-muted-foreground">
                        row {workspace.rowVersion} · {formatDate(workspace.updatedAt)}
                      </div>
                    </div>
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      onClick={() => setArchiveTarget(workspace)}
                    >
                      <Archive className="h-4 w-4" />
                      归档
                    </Button>
                  </div>
                ))}
              </div>
              {archivedWorkspaces.length > 0 && (
                <>
                  <Separator className="my-6" />
                  <div className="space-y-3">
                    <div className="font-mono text-xs uppercase tracking-wider text-muted-foreground">
                      已归档
                    </div>
                    {archivedWorkspaces.map((workspace) => (
                      <div
                        key={workspace.id}
                        className="border border-border/70 p-4 text-muted-foreground"
                      >
                        <div className="font-medium">{workspace.name}</div>
                        <div className="mt-1 font-mono text-xs">
                          row {workspace.rowVersion} · {formatDate(workspace.updatedAt)}
                        </div>
                      </div>
                    ))}
                  </div>
                </>
              )}
            </CardContent>
          </Card>
        </section>

        <aside className="space-y-4">
          <Card className="rounded-none">
            <CardHeader>
              <CardTitle className="text-base">当前可用边界</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3 text-sm">
              <div className="flex items-start gap-3">
                <Database className="mt-0.5 h-4 w-4 shrink-0" />
                <span>Owner、Workspace 与审计保存在独立 SQLite。</span>
              </div>
              <div className="flex items-start gap-3 text-muted-foreground">
                <Bot className="mt-0.5 h-4 w-4 shrink-0" />
                <span>Agent、Provider、RAG 与 MCP 尚未接入，调用保持关闭。</span>
              </div>
              <div className="flex items-start gap-3 text-muted-foreground">
                <HardDrive className="mt-0.5 h-4 w-4 shrink-0" />
                <span>归档只改变本机元数据，不删除用户数据目录。</span>
              </div>
            </CardContent>
          </Card>
          <Card className="rounded-none">
            <CardHeader>
              <CardTitle className="text-base">工程版本声明</CardTitle>
              <CardDescription>
                P6.6 R0 只证明本机产品准入；不代表已签名、可分发或生产就绪。
              </CardDescription>
            </CardHeader>
          </Card>
        </aside>
      </div>

      <Dialog
        open={archiveTarget !== null}
        onOpenChange={(open) => !open && setArchiveTarget(null)}
      >
        <DialogContent className="rounded-none">
          <DialogHeader>
            <DialogTitle>归档工作空间</DialogTitle>
            <DialogDescription>
              {archiveTarget
                ? `“${archiveTarget.name}”将从活动列表移入归档。P6.6 R0 不提供恢复操作。`
                : '请选择要归档的工作空间。'}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              onClick={() => setArchiveTarget(null)}
              disabled={submitting}
            >
              取消
            </Button>
            <Button type="button" onClick={() => void archiveWorkspace()} disabled={submitting}>
              {submitting && <Loader2 className="h-4 w-4 animate-spin" />}
              确认归档
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </main>
  )
}
