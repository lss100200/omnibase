'use client'

import { FormEvent, useCallback, useEffect, useRef, useState } from 'react'
import {
  Database,
  HardDrive,
  Loader2,
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
import { DesktopWorkbench } from './workbench-client'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
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

export default function DesktopAdmissionPage() {
  const bridgeRef = useRef<OmniBaseDesktopBridge | null>(null)
  const [loadState, setLoadState] = useState<LoadState>('loading')
  const [version, setVersion] = useState('1.0.0')
  const [owner, setOwner] = useState<DesktopOwner | null>(null)
  const [workspaces, setWorkspaces] = useState<readonly DesktopWorkspace[]>([])
  const [displayName, setDisplayName] = useState('')
  const [workspaceName, setWorkspaceName] = useState('我的工作空间')
  const [submitting, setSubmitting] = useState(false)
  const [operationError, setOperationError] = useState<string | null>(null)

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
                Desktop Local / P6.7 R0
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
                    OmniBase {version} · 未签名工程验收版本。建立 Owner 后可配置你自己的
                    Provider，并与本机父 Agent 对话。
              </p>
            </CardContent>
          </Card>
        </div>
      </main>
    )
  }

  const bridge = bridgeRef.current
  if (bridge === null) {
    return (
      <main className="flex min-h-screen items-center justify-center bg-background p-8">
        <Card className="w-full max-w-xl rounded-none">
          <CardHeader>
            <CardTitle>仅可从 OmniBase 桌面应用访问</CardTitle>
          </CardHeader>
        </Card>
      </main>
    )
  }

  return (
    <>
      {operationError && (
        <div role="alert" className="border-b border-destructive/40 bg-destructive/10 px-5 py-2 text-[15px]">
          {operationError}
        </div>
      )}
      <DesktopWorkbench
        bridge={bridge}
        owner={owner}
        version={version}
        workspaces={workspaces}
        onWorkspacesChange={setWorkspaces}
        onError={setOperationError}
      />
    </>
  )
}
