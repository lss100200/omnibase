'use client'

import { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  Archive,
  Bot,
  Loader2,
  Plus,
  Send,
  ShieldCheck,
  Square,
  RotateCcw,
} from 'lucide-react'
import {
  DesktopConversation,
  DesktopMessage,
  DesktopOwner,
  DesktopProvider,
  DesktopWorkspace,
  OmniBaseDesktopBridge,
  applyDesktopConversationEvent,
  beginDesktopLiveSend,
  completeDesktopLiveSend,
  createDesktopLiveStreamState,
  desktopLiveStopVisible,
  desktopLiveViewIsOrigin,
  requestDesktopLiveCancel,
  switchDesktopLiveScope,
  type DesktopReasoningGear,
  type DesktopThinkingDepth,
} from '@/lib/desktop-bridge'
import {
  advanceDesktopSurfaceScope,
  applyDesktopScopedProjection,
  createDesktopSurfaceScope,
  desktopSurfaceProjectionIsCurrent,
  type DesktopSurfaceScope,
} from '@/lib/desktop-surface-scope'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Separator } from '@/components/ui/separator'

const ERROR_MESSAGES: Readonly<Record<string, string>> = {
  desktop_native_input_invalid: '输入不符合本机控制边界。',
  desktop_native_request_failed: '本机服务暂时不可用。',
  desktop_native_response_invalid: '本机服务返回了无法验证的数据。',
  desktop_owner_not_initialized: '请先建立本机 Owner。',
  desktop_runtime_not_ready: '本机运行时尚未就绪。',
  desktop_workspace_not_found: '工作空间不存在或已被移除。',
  desktop_workspace_archived: '工作空间已归档。',
  desktop_provider_endpoint_invalid: 'Provider 地址不符合安全边界。',
  desktop_provider_secret_required: '请填写 API Key。',
  desktop_provider_ambiguous: '请选择一个已启用的默认 Provider。',
  desktop_provider_not_found: '找不到该 Provider。',
  desktop_secret_vault_unavailable: '本机凭据保险库不可用。',
  desktop_conversation_not_found: '会话不存在。',
  desktop_invocation_in_progress: '当前仍有生成进行中。',
  desktop_invocation_cancelled: '生成已停止',
}

function errorMessage(code: string): string {
  return ERROR_MESSAGES[code] ?? '操作未完成；本机服务已安全拒绝该请求。'
}

function familyLabel(family: string): string {
  switch (family) {
    case 'deepseek':
      return 'DeepSeek'
    case 'openai':
      return 'OpenAI / GPT'
    case 'anthropic':
      return 'Claude'
    case 'glm':
      return 'GLM'
    case 'kimi':
      return 'Kimi'
    default:
      return 'OpenAI 兼容'
  }
}

function statusLabel(status: string): string {
  switch (status) {
    case 'succeeded':
      return '已完成'
    case 'failed':
      return '失败'
    case 'cancelled':
      return '调用已取消'
    case 'unknown':
      return '调用状态未知'
    case 'running':
    case 'streaming':
      return '生成中'
    default:
      return status
  }
}

export function DesktopWorkbench({
  bridge,
  owner,
  version,
  workspaces,
  onWorkspacesChange,
  onError,
}: {
  readonly bridge: OmniBaseDesktopBridge
  readonly owner: DesktopOwner
  readonly version: string
  readonly workspaces: readonly DesktopWorkspace[]
  readonly onWorkspacesChange: (items: readonly DesktopWorkspace[]) => void
  readonly onError: (message: string | null) => void
}) {
  const chinese = useMemo(
    () => (typeof navigator === 'undefined' ? true : navigator.language.toLowerCase().startsWith('zh')),
    [],
  )
  const [zoom, setZoom] = useState(100)
  const initialWorkspaceId = workspaces.find((item) => item.state === 'active')?.id ?? null
  const [workspaceId, setWorkspaceId] = useState<string | null>(initialWorkspaceId)
  const [conversations, setConversations] = useState<readonly DesktopConversation[]>([])
  const [conversationId, setConversationId] = useState<string | null>(null)
  const [messages, setMessages] = useState<readonly DesktopMessage[]>([])
  const [providers, setProviders] = useState<readonly DesktopProvider[]>([])
  const [agentName, setAgentName] = useState('父 Agent')
  const [draft, setDraft] = useState('')
  const [live, setLive] = useState(() =>
    createDesktopLiveStreamState({
      workspaceId: initialWorkspaceId,
      conversationId: null,
    }),
  )
  const liveRef = useRef(live)
  liveRef.current = live
  const surfaceScopeRef = useRef<DesktopSurfaceScope>(
    createDesktopSurfaceScope(initialWorkspaceId, null),
  )

  const applyViewScope = useCallback((nextWorkspaceId: string | null, nextConversationId: string | null) => {
    const next = advanceDesktopSurfaceScope(
      surfaceScopeRef.current,
      nextWorkspaceId,
      nextConversationId,
    )
    surfaceScopeRef.current = next
    setWorkspaceId(next.workspaceId)
    setConversationId(next.conversationId)
    return next
  }, [])
  const [detailsOpen, setDetailsOpen] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [providerForm, setProviderForm] = useState({
    displayName: '',
    baseUrl: '',
    apiKey: '',
    modelName: '',
    gear: 'standard' as DesktopReasoningGear,
    thinkingDepth: 'medium' as DesktopThinkingDepth,
    timeoutSeconds: 30,
    allowLoopbackHttp: false,
    isDefault: true,
    isEnabled: true,
  })
  const [testResult, setTestResult] = useState<string | null>(null)
  const [workspaceName, setWorkspaceName] = useState('')
  const bottomRef = useRef<HTMLDivElement | null>(null)

  const activeWorkspaces = workspaces.filter((item) => item.state === 'active')

  const loadWorkspaceSurface = useCallback(
    async (nextWorkspaceId: string) => {
      const started = surfaceScopeRef.current
      const [conversationResult, providerResult, agentResult] = await Promise.all([
        bridge.conversations.list({ workspaceId: nextWorkspaceId }),
        bridge.providers.list(),
        bridge.workspaces.agent({ workspaceId: nextWorkspaceId }),
      ])
      if (!desktopSurfaceProjectionIsCurrent(started, surfaceScopeRef.current)) return
      if (!conversationResult.ok) {
        onError(errorMessage(conversationResult.error.code))
        return
      }
      if (!providerResult.ok) {
        onError(errorMessage(providerResult.error.code))
        return
      }
      if (agentResult.ok) setAgentName(agentResult.value.agent.displayName)
      setProviders(providerResult.value.items)
      const active = conversationResult.value.items.filter((item) => item.state === 'active')
      setConversations(conversationResult.value.items)
      const selected = active[0]
      const selectedScope = applyViewScope(nextWorkspaceId, selected?.id ?? null)
      if (selected === undefined) {
        setMessages([])
        return
      }
      const detail = await bridge.conversations.get({
        workspaceId: nextWorkspaceId,
        conversationId: selected.id,
      })
      if (!detail.ok) return
      setMessages((current) =>
        applyDesktopScopedProjection(selectedScope, surfaceScopeRef.current, current, detail.value.messages),
      )
    },
    [applyViewScope, bridge, onError],
  )

  useEffect(() => {
    if (workspaceId !== null) void loadWorkspaceSurface(workspaceId)
  }, [loadWorkspaceSurface, workspaceId])

  useEffect(() => {
    setLive((current) => {
      const next = switchDesktopLiveScope(current, workspaceId, conversationId)
      liveRef.current = next
      return next
    })
  }, [conversationId, workspaceId])

  useEffect(() => {
    return bridge.conversations.subscribe((event) => {
      const current = liveRef.current
      const next = applyDesktopConversationEvent(current, event)
      liveRef.current = next
      setLive(next)
      if (next.cancelRequested && event.type === 'identity' && next.liveInvocation !== null) {
        void bridge.conversations.cancel({ invocationId: next.liveInvocation })
      }
      if (event.type === 'cancelled' && event.invocationId === current.liveInvocation) {
        onError('生成已停止')
      }
    })
  }, [bridge, onError])

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ block: 'end' })
  }, [messages, live.liveText])

  const createConversation = async (): Promise<string | null> => {
    if (workspaceId === null) return null
    const started = surfaceScopeRef.current
    const created = await bridge.conversations.create({ workspaceId })
    if (!created.ok) {
      onError(errorMessage(created.error.code))
      return null
    }
    if (!desktopSurfaceProjectionIsCurrent(started, surfaceScopeRef.current)) return null
    setConversations((current) => [created.value.conversation, ...current])
    applyViewScope(workspaceId, created.value.conversation.id)
    setMessages([])
    return created.value.conversation.id
  }

  const ensureConversation = async (): Promise<string | null> => {
    if (workspaceId === null) return null
    if (conversationId !== null) return conversationId
    return createConversation()
  }

  const createWorkspace = async (event: FormEvent) => {
    event.preventDefault()
    const name = workspaceName.trim()
    if (name === '') return
    const result = await bridge.workspaces.create({ name })
    if (!result.ok) {
      onError(errorMessage(result.error.code))
      return
    }
    const next = [
      ...workspaces.filter((item) => item.id !== result.value.workspace.id),
      result.value.workspace,
    ]
    onWorkspacesChange(next)
    applyViewScope(result.value.workspace.id, null)
    setConversations([])
    setMessages([])
    setWorkspaceName('')
  }

  const archiveCurrentConversation = async () => {
    const current = conversations.find((item) => item.id === conversationId)
    if (workspaceId === null || current === undefined) return
    const result = await bridge.conversations.archive({
      workspaceId,
      conversationId: current.id,
      expectedRowVersion: current.rowVersion,
    })
    if (!result.ok) {
      onError(errorMessage(result.error.code))
      return
    }
    setConversations((items) =>
      items.map((item) => (item.id === result.value.conversation.id ? result.value.conversation : item)),
    )
    const remaining = conversations.filter(
      (item) => item.id !== current.id && item.state === 'active',
    )
    applyViewScope(workspaceId, remaining[0]?.id ?? null)
    setMessages([])
  }

  const sendMessage = async (event: FormEvent) => {
    event.preventDefault()
    const content = draft.trim()
    if (workspaceId === null || content === '' || desktopLiveStopVisible(live)) return
    const target = await ensureConversation()
    if (target === null) return
    const started = surfaceScopeRef.current
    onError(null)
    setDraft('')
    const nextLive = beginDesktopLiveSend({
      ...liveRef.current,
      workspaceId,
      conversationId: target,
    })
    liveRef.current = nextLive
    setLive(nextLive)
    const result = await bridge.conversations.send({
      workspaceId,
      conversationId: target,
      content,
    })
    const completed = completeDesktopLiveSend(liveRef.current, nextLive.sendGeneration)
    liveRef.current = completed
    setLive(completed)
    if (!desktopSurfaceProjectionIsCurrent(started, surfaceScopeRef.current)) return
    if (!result.ok) {
      onError(errorMessage(result.error.code))
      return
    }
    const detail = await bridge.conversations.get({ workspaceId, conversationId: target })
    if (!detail.ok) return
    if (!desktopSurfaceProjectionIsCurrent(started, surfaceScopeRef.current)) return
    setMessages(detail.value.messages)
    setConversations((current) =>
      current.map((item) => (item.id === detail.value.conversation.id ? detail.value.conversation : item)),
    )
  }

  const stopGeneration = async () => {
    const current = liveRef.current
    if (!desktopLiveStopVisible(current) && current.liveInvocation === null) return
    const cancelled = requestDesktopLiveCancel(current)
    liveRef.current = cancelled
    setLive(cancelled)
    onError('生成已停止')
    if (current.liveInvocation !== null) {
      await bridge.conversations.cancel({ invocationId: current.liveInvocation })
    }
  }

  const retryLast = async () => {
    if (workspaceId === null || conversationId === null || desktopLiveStopVisible(live)) return
    const failed = [...messages].reverse().find((item) => item.role === 'assistant' && item.status !== 'completed')
    if (failed === undefined) return
    const started = surfaceScopeRef.current
    const nextLive = beginDesktopLiveSend(liveRef.current)
    liveRef.current = nextLive
    setLive(nextLive)
    const result = await bridge.conversations.send({
      workspaceId,
      conversationId,
      content: '',
      retryOfMessageId: failed.id,
    })
    const completed = completeDesktopLiveSend(liveRef.current, nextLive.sendGeneration)
    liveRef.current = completed
    setLive(completed)
    if (!desktopSurfaceProjectionIsCurrent(started, surfaceScopeRef.current)) return
    if (!result.ok) {
      onError(errorMessage(result.error.code))
      return
    }
    const detail = await bridge.conversations.get({ workspaceId, conversationId })
    if (!detail.ok) return
    setMessages((current) =>
      applyDesktopScopedProjection(started, surfaceScopeRef.current, current, detail.value.messages),
    )
  }

  const saveProvider = async (event: FormEvent) => {
    event.preventDefault()
    setSubmitting(true)
    onError(null)
    try {
      const result = await bridge.providers.upsert({
        displayName: providerForm.displayName.trim(),
        baseUrl: providerForm.baseUrl.trim(),
        apiKey: providerForm.apiKey.trim() === '' ? undefined : providerForm.apiKey,
        modelName: providerForm.modelName.trim(),
        gear: providerForm.gear,
        thinkingDepth: providerForm.thinkingDepth,
        timeoutSeconds: providerForm.timeoutSeconds,
        allowLoopbackHttp: providerForm.allowLoopbackHttp,
        isDefault: providerForm.isDefault,
        isEnabled: providerForm.isEnabled,
      })
      if (!result.ok) {
        onError(errorMessage(result.error.code))
        return
      }
      const listed = await bridge.providers.list()
      if (listed.ok) setProviders(listed.value.items)
      setProviderForm((current) => ({ ...current, apiKey: '' }))
      setTestResult('Provider 已保存。API Key 不会回读到界面。')
    } finally {
      setSubmitting(false)
    }
  }

  const testSelected = async (providerId: string) => {
    const result = await bridge.providers.test({ providerId })
    if (!result.ok) {
      onError(errorMessage(result.error.code))
      return
    }
    setTestResult(
      result.value.ok
        ? `测试通过 · ${familyLabel(result.value.family)} · ${
            result.value.identityProven
              ? (result.value.actualModel ?? result.value.requestedModel)
              : '模型身份未证明'
          }`
        : result.value.errorRedacted ?? '测试失败',
    )
  }

  return (
    <div className="min-h-screen bg-background" style={{ fontSize: `${(16 * zoom) / 100}px` }}>
      <header className="border-b border-border bg-card/70">
        <div className="mx-auto flex max-w-7xl flex-col gap-3 px-5 py-5 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <div className="font-mono text-[13px] uppercase tracking-[0.16em] text-foreground/80">
              OmniBase {version} / {chinese ? '本机单 Agent' : 'Desktop agent'}
            </div>
            <h1 className="mt-1 text-[22px] font-semibold tracking-tight">{owner.displayName}</h1>
          </div>
          <div className="flex items-center gap-2 text-[13px]">
            <Badge variant="outline" className="rounded-none">
              <ShieldCheck className="mr-1 h-3.5 w-3.5" />
              原生控制
            </Badge>
            {desktopLiveStopVisible(live) && (
              <Button type="button" variant="outline" size="sm" onClick={() => void stopGeneration()}>
                <Square className="h-4 w-4" />
                停止
              </Button>
            )}
            <Button type="button" variant="outline" size="sm" onClick={() => setZoom((value) => Math.max(90, value - 10))}>
              A-
            </Button>
            <Button type="button" variant="outline" size="sm" onClick={() => setZoom((value) => Math.min(140, value + 10))}>
              A+
            </Button>
          </div>
        </div>
      </header>

      <div className="mx-auto grid max-w-7xl gap-4 px-5 py-5 lg:grid-cols-[240px_minmax(0,1fr)_320px]">
        <aside className="space-y-4">
          <Card className="rounded-none">
            <CardHeader className="pb-3">
              <CardTitle className="text-base">工作空间</CardTitle>
            </CardHeader>
            <CardContent className="space-y-2">
              <form className="flex gap-2" onSubmit={(event) => void createWorkspace(event)}>
                <Input
                  value={workspaceName}
                  onChange={(event) => setWorkspaceName(event.target.value)}
                  placeholder="新工作空间"
                  className="h-9 rounded-none text-[13px]"
                />
                <Button type="submit" size="sm" variant="outline" disabled={workspaceName.trim() === ''}>
                  <Plus className="h-4 w-4" />
                </Button>
              </form>
              {activeWorkspaces.map((workspace) => (
                <button
                  key={workspace.id}
                  type="button"
                  className={`w-full border px-3 py-2 text-left text-[15px] ${
                    workspace.id === workspaceId ? 'border-foreground bg-accent' : 'border-border'
                  }`}
                  onClick={() => {
                    applyViewScope(workspace.id, null)
                    setConversations([])
                    setMessages([])
                  }}
                >
                  {workspace.name}
                </button>
              ))}
            </CardContent>
          </Card>
          <Card className="rounded-none">
            <CardHeader className="pb-3">
              <CardTitle className="flex items-center justify-between text-base">
                会话
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  disabled={workspaceId === null}
                  onClick={() => void createConversation()}
                >
                  <Plus className="h-4 w-4" />
                  新建
                </Button>
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-2">
              {conversations
                .filter((item) => item.state === 'active')
                .map((item) => (
                  <button
                    key={item.id}
                    type="button"
                    className={`w-full border px-3 py-2 text-left text-[15px] ${
                      item.id === conversationId ? 'border-foreground bg-accent' : 'border-border'
                    }`}
                    onClick={() => {
                      const started = applyViewScope(workspaceId, item.id)
                      if (workspaceId === null) return
                      void bridge.conversations
                        .get({ workspaceId, conversationId: item.id })
                        .then((detail) => {
                          if (!detail.ok) return
                          setMessages((current) =>
                            applyDesktopScopedProjection(
                              started,
                              surfaceScopeRef.current,
                              current,
                              detail.value.messages,
                            ),
                          )
                        })
                    }}
                  >
                    {item.title}
                  </button>
                ))}
            </CardContent>
          </Card>
        </aside>

        <section className="flex min-h-[70vh] flex-col border border-border bg-card">
          <div className="flex items-center justify-between border-b border-border px-4 py-3">
            <div className="flex items-center gap-2 text-[16px] font-medium">
              <Bot className="h-4 w-4" />
              {agentName}
            </div>
            <div className="flex gap-2">
              {conversationId !== null && (
                <Button type="button" variant="outline" size="sm" onClick={() => void archiveCurrentConversation()}>
                  <Archive className="h-4 w-4" />
                  归档会话
                </Button>
              )}
              {desktopLiveStopVisible(live) && (
                <Button type="button" variant="outline" size="sm" onClick={() => void stopGeneration()}>
                  <Square className="h-4 w-4" />
                  停止
                </Button>
              )}
            </div>
          </div>
          <div className="flex-1 space-y-4 overflow-y-auto p-4 text-[16px] leading-7">
            {messages.map((message) => (
              <div key={message.id} className="space-y-1">
                <div className="text-[13px] font-medium text-foreground">
                  {message.role === 'user' ? '你' : agentName}
                </div>
                <div className="whitespace-pre-wrap break-words">
                  {message.content || (message.status === 'cancelled' ? '生成已停止' : '')}
                </div>
                {message.retryOfMessageId && (
                  <div className="text-[13px] text-foreground/80">重试自前一次调用</div>
                )}
                {message.invocation && (
                  <div className="text-[13px] text-foreground/80">
                    {statusLabel(message.invocation.status)}
                    {message.invocation.retryOfInvocationId ? ' · 新调用' : ''}
                    {message.invocation.errorRedacted ? ` · ${message.invocation.errorRedacted}` : ''}
                  </div>
                )}
              </div>
            ))}
            {desktopLiveViewIsOrigin(live) && live.streaming && live.liveText !== '' && (
              <div className="whitespace-pre-wrap break-words">{live.liveText}</div>
            )}
            <div ref={bottomRef} />
          </div>
          <form className="border-t border-border p-4" onSubmit={(event) => void sendMessage(event)}>
            <textarea
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              rows={3}
              placeholder="向父 Agent 提问…"
              className="w-full resize-none border border-input bg-background p-3 text-[16px] outline-none"
            />
            <div className="mt-3 flex items-center justify-between gap-2">
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={() => void retryLast()}
                disabled={desktopLiveStopVisible(live)}
              >
                <RotateCcw className="h-4 w-4" />
                重试
              </Button>
              <Button type="submit" disabled={desktopLiveStopVisible(live) || draft.trim() === ''}>
                {desktopLiveStopVisible(live) ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <Send className="h-4 w-4" />
                )}
                发送
              </Button>
            </div>
          </form>
          {live.liveMeta && (
            <button
              type="button"
              className="border-t border-border px-4 py-2 text-left text-[13px]"
              onClick={() => setDetailsOpen((value) => !value)}
            >
              调用详情 {detailsOpen ? '收起' : '展开'}
              {detailsOpen && (
                <div className="mt-2 space-y-1 text-foreground">
                  <div>请求模型：{live.liveMeta.requestedModel ?? '—'}</div>
                  <div>实际模型：{live.liveMeta.actualModel ?? '—'}</div>
                  <div>Provider：{live.liveMeta.providerName ?? '—'}</div>
                  <div>状态：{statusLabel(live.liveMeta.status ?? 'running')}</div>
                  <div>耗时：{live.liveMeta.durationMs ?? '—'} ms</div>
                  <div>Tokens：{live.liveMeta.totalTokens ?? '未提供'}</div>
                  <div>思考深度：{live.liveMeta.thinkingDepth ?? '—'}</div>
                  {live.liveMeta.errorRedacted && <div>错误：{live.liveMeta.errorRedacted}</div>}
                </div>
              )}
            </button>
          )}
        </section>

        <aside className="space-y-4">
          <Card className="rounded-none">
            <CardHeader>
              <CardTitle className="text-base">模型 Provider</CardTitle>
              <CardDescription className="text-[13px]">
                API Key 只用于保存或测试，不会再显示。
              </CardDescription>
            </CardHeader>
            <CardContent>
              <form className="space-y-3" onSubmit={(event) => void saveProvider(event)}>
                <div className="space-y-1">
                  <Label>显示名称</Label>
                  <Input
                    value={providerForm.displayName}
                    onChange={(event) =>
                      setProviderForm((current) => ({ ...current, displayName: event.target.value }))
                    }
                    className="h-10 rounded-none text-[15px]"
                  />
                </div>
                <div className="space-y-1">
                  <Label>Base URL</Label>
                  <Input
                    value={providerForm.baseUrl}
                    onChange={(event) =>
                      setProviderForm((current) => ({ ...current, baseUrl: event.target.value }))
                    }
                    placeholder="https://api.deepseek.com/v1"
                    className="h-10 rounded-none text-[15px]"
                  />
                </div>
                <div className="space-y-1">
                  <Label>API Key</Label>
                  <Input
                    type="password"
                    value={providerForm.apiKey}
                    onChange={(event) =>
                      setProviderForm((current) => ({ ...current, apiKey: event.target.value }))
                    }
                    autoComplete="off"
                    className="h-10 rounded-none text-[15px]"
                  />
                </div>
                <div className="space-y-1">
                  <Label>模型名称</Label>
                  <Input
                    value={providerForm.modelName}
                    onChange={(event) =>
                      setProviderForm((current) => ({ ...current, modelName: event.target.value }))
                    }
                    placeholder="deepseek-chat"
                    className="h-10 rounded-none text-[15px]"
                  />
                </div>
                <div className="grid grid-cols-2 gap-2 text-[13px]">
                  <label className="space-y-1">
                    档位
                    <select
                      value={providerForm.gear}
                      onChange={(event) =>
                        setProviderForm((current) => ({
                          ...current,
                          gear: event.target.value as DesktopReasoningGear,
                        }))
                      }
                      className="h-10 w-full border border-input bg-background px-2"
                    >
                      <option value="economy">经济</option>
                      <option value="standard">标准</option>
                      <option value="deep">深度</option>
                      <option value="audit">审计</option>
                    </select>
                  </label>
                  <label className="space-y-1">
                    思考深度
                    <select
                      value={providerForm.thinkingDepth}
                      onChange={(event) =>
                        setProviderForm((current) => ({
                          ...current,
                          thinkingDepth: event.target.value as DesktopThinkingDepth,
                        }))
                      }
                      className="h-10 w-full border border-input bg-background px-2"
                    >
                      <option value="disabled">关闭</option>
                      <option value="low">低</option>
                      <option value="medium">中</option>
                      <option value="high">高</option>
                    </select>
                  </label>
                </div>
                <label className="flex items-center gap-2 text-[13px]">
                  <input
                    type="checkbox"
                    checked={providerForm.allowLoopbackHttp}
                    onChange={(event) =>
                      setProviderForm((current) => ({
                        ...current,
                        allowLoopbackHttp: event.target.checked,
                      }))
                    }
                  />
                  允许本机 HTTP（127.0.0.1 / localhost）
                </label>
                <Button type="submit" className="w-full rounded-none" disabled={submitting}>
                  保存 Provider
                </Button>
              </form>
              {testResult && <p className="mt-3 text-[13px]">{testResult}</p>}
              <Separator className="my-4" />
              <div className="space-y-2">
                {providers.map((provider) => (
                  <div key={provider.id} className="border border-border p-3 text-[13px]">
                    <div className="font-medium text-[15px]">{provider.displayName}</div>
                    <div>
                      {familyLabel(provider.family)} · {provider.modelName}
                    </div>
                    <Button
                      type="button"
                      size="sm"
                      variant="outline"
                      className="mt-2"
                      onClick={() => void testSelected(provider.id)}
                    >
                      测试
                    </Button>
                  </div>
                ))}
              </div>
            </CardContent>
          </Card>
        </aside>
      </div>
    </div>
  )
}
