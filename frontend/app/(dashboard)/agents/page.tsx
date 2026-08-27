'use client'

import Link from 'next/link'
import { useEffect, useRef, useState } from 'react'
import {
  Bot,
  BrainCircuit,
  Database,
  Loader2,
  Send,
  Settings2,
  ShieldCheck,
  Square,
  UserPlus,
  Wrench,
} from 'lucide-react'
import {
  agentBuilderApi,
  agentAlphaApi,
  workspacesApi,
  type AgentAlphaProfile,
  type AgentAlphaProfileList,
} from '@/lib/api'
import {
  agentInvokeConditionsMet,
  canInvokeAgent,
  personalRuntimeInvokeConditionsMet,
} from '@/lib/personal-runtime-gate'
import { isUserCancelledError } from '@/lib/cancel-detection'
import { consumeAgentAlphaStream } from '@/lib/agent-alpha-stream'
import { InvocationGuard, type InvocationPhase } from '@/lib/invocation-state'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
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
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'

interface WorkbenchMessage {
  readonly id: string
  readonly role: 'user' | 'agent'
  readonly content: string
  readonly citations?: ReadonlyArray<{
    readonly index: number
    readonly chunk_id: string
    readonly document_id: string
    readonly snippet: string
  }>
}

interface UsageInfo {
  readonly input_tokens?: number
  readonly output_tokens?: number
  readonly total_tokens?: number
}

export default function AgentAlphaPage() {
  const [workspaces, setWorkspaces] = useState<Array<{ id: string; display_name: string }>>([])
  const [workspaceId, setWorkspaceId] = useState('')
  const [installations, setInstallations] = useState<AgentAlphaProfile[]>([])
  const [bindingId, setBindingId] = useState('')
  const [input, setInput] = useState('')
  const [messages, setMessages] = useState<WorkbenchMessage[]>([])
  const [streaming, setStreaming] = useState('')
  const [invocationId, setInvocationId] = useState<string | null>(null)
  const [taskId, setTaskId] = useState<string | null>(null)
  const [identity, setIdentity] = useState('调用后将在这里显示模型服务与模型身份')
  const [usage, setUsage] = useState<UsageInfo | null>(null)
  const [latencyMs, setLatencyMs] = useState<number | null>(null)
  const [posture, setPosture] = useState<{
    engineering_assembled: boolean
    lite_gate_enabled: boolean
    engineering_flag_enabled: boolean
    environment_allowed: boolean
    phase5_gates_all_false: boolean
    production_activation_allowed: boolean
    tools_enabled: boolean
    multi_agent_enabled: boolean
    formal_builder: string
    alpha_builder: string
    supported_invocation_modes: string[]
    formal_builder_integration: string
    engineering_composition_ready: boolean
    activation_allowed: boolean
    expected_migration_head: string
    runtime_profile: string
    personal_runtime_state: string
    personal_runtime_active: boolean
    personal_canary_id: string | null
    personal_canary_expires_at: string | null
  } | null>(null)
  const [postureLoading, setPostureLoading] = useState(false)
  const [statusError, setStatusError] = useState<string | null>(null)
  const [builderOpen, setBuilderOpen] = useState(false)
  const [builderSaving, setBuilderSaving] = useState(false)
  const [builderMessage, setBuilderMessage] = useState<string | null>(null)
  const [builder, setBuilder] = useState({
    displayName: '',
    roleDescription: '',
    instructions: '',
    assistantTone: '专业、简洁，并明确说明不确定性。',
    maxContextTokens: 16_384,
    maxOutputTokens: 2_048,
    maxWallClockSeconds: 120,
  })
  const guardRef = useRef<InvocationGuard | null>(null)
  if (guardRef.current === null) guardRef.current = new InvocationGuard()
  const [phase, setPhase] = useState<InvocationPhase>('idle')
  const controllerRef = useRef<AbortController | null>(null)
  const startedAtRef = useRef<number | null>(null)

  useEffect(() => {
    workspacesApi
      .list()
      .then((list: { items: Array<{ id: string; display_name: string }> } | undefined) => {
        const items = Array.isArray(list?.items) ? list.items : []
        setWorkspaces(items)
        const first = items[0]
        if (items.length === 1 && first) setWorkspaceId(first.id)
      })
      .catch(() => setWorkspaces([]))
  }, [])

  useEffect(() => {
    setInstallations([])
    setBindingId('')
    if (!workspaceId) return
    setPostureLoading(true)
    agentAlphaApi
      .profiles(workspaceId)
      .then((list: AgentAlphaProfileList | undefined) => {
        const items = list?.items ?? []
        setInstallations(items)
        const first = items[0]
        if (items.length === 1 && first) setBindingId(first.agent_version_id)
      })
      .catch(() => setInstallations([]))
    agentAlphaApi
      .status(workspaceId)
      .then((status) => {
        setPosture(status)
        setStatusError(null)
      })
      .catch(() => setStatusError('agent_alpha_unavailable'))
      .finally(() => setPostureLoading(false))
  }, [workspaceId])

  const selectedInstallation = installations.find((item) => item.agent_version_id === bindingId)

  const createAgent = async () => {
    if (!workspaceId || builderSaving) return
    setBuilderSaving(true)
    setBuilderMessage(null)
    try {
      const result = await agentBuilderApi.create(workspaceId, {
        display_name: builder.displayName,
        role_description: builder.roleDescription,
        instructions: builder.instructions,
        assistant_tone: builder.assistantTone,
        provider_policy: 'user_default',
        knowledge_mode: 'workspace_read_only',
        max_context_tokens: builder.maxContextTokens,
        max_output_tokens: builder.maxOutputTokens,
        max_wall_clock_seconds: builder.maxWallClockSeconds,
        install_immediately: true,
      })
      const createdMessage = `${result.definition.display_name} 已封存并安装。`
      setBuilderMessage(createdMessage)
      try {
        const profiles = await agentAlphaApi.profiles(workspaceId)
        setInstallations(profiles.items)
        setBindingId(result.version.agent_version_id)
      } catch {
        // Creation is an atomic Registry success even when the separately
        // gated Alpha profile resolver is not assembled in this environment.
        // Do not misreport a successful durable write as a failed Builder call.
        setBuilderMessage(
          `${createdMessage} 暂时无法刷新运行配置；Agent Alpha 完成装配后会自动显示。`,
        )
      }
      setBuilder({
        displayName: '',
        roleDescription: '',
        instructions: '',
        assistantTone: '专业、简洁，并明确说明不确定性。',
        maxContextTokens: 16_384,
        maxOutputTokens: 2_048,
        maxWallClockSeconds: 120,
      })
    } catch (error) {
      setBuilderMessage(error instanceof Error ? error.message : 'agent_builder_failed')
    } finally {
      setBuilderSaving(false)
    }
  }

  const invoke = async () => {
    const userMessage = input.trim()
    if (!canInvokeAgent(posture, userMessage, workspaceId, bindingId)) return
    // P1-5: a unique generation owns each invocation; begin() is refused
    // while the previous promise is still running or cancelling.
    const guard = guardRef.current!
    const started = guard.begin()
    if (started === null) return
    const { generation, controller } = started
    controllerRef.current = controller
    setPhase('running')
    setMessages((current) => [
      ...current,
      { id: crypto.randomUUID(), role: 'user', content: userMessage },
    ])
    setInput('')
    setStreaming('')
    setUsage(null)
    setLatencyMs(null)
    setTaskId(null)
    setInvocationId(null)
    startedAtRef.current = performance.now()
    try {
      const response = await agentAlphaApi.invokeStream(
        workspaceId,
        { agent_version_id: bindingId, message: userMessage, top_k: 5 },
        { signal: controller.signal },
      )
      if (!response.ok || !response.body) {
        const payload = (await response.json().catch(() => null)) as {
          error?: { code?: string }
          detail?: { error?: { code?: string } }
        } | null
        const code =
          payload?.error?.code ?? payload?.detail?.error?.code ?? `HTTP ${response.status}`
        throw new Error(code)
      }
      const terminal = await consumeAgentAlphaStream(response.body.getReader(), {
        onMeta: (meta) => {
          if (!guard.isCurrent(generation)) return
          setInvocationId(meta.invocationId)
          setTaskId(meta.taskId)
          if (meta.identity) setIdentity(meta.identity)
        },
        onChunk: (content) => {
          if (!guard.isCurrent(generation)) return
          setStreaming((current) => current + content)
        },
        onCitations: (citations) => {
          // Citations are collected by the consumer and delivered on the
          // `done` terminal; no live citation state is needed.
          void citations
        },
        onUsage: (usage) => {
          if (!guard.isCurrent(generation)) return
          setUsage(
            usage === null
              ? null
              : {
                  input_tokens: usage.input_tokens,
                  output_tokens: usage.output_tokens,
                  total_tokens: usage.total_tokens,
                },
          )
        },
      })
      if (!guard.isCurrent(generation)) return
      if (terminal.kind === 'done') {
        if (startedAtRef.current !== null) {
          setLatencyMs(Math.round(performance.now() - startedAtRef.current))
        }
        setMessages((current) => [
          ...current,
          {
            id: crypto.randomUUID(),
            role: 'agent',
            content: terminal.answer || '模型没有返回答案。',
            citations: terminal.citations,
          },
        ])
        setStreaming('')
      } else if (terminal.kind === 'cancelled') {
        setMessages((current) => [
          ...current,
          { id: crypto.randomUUID(), role: 'agent', content: '本次调用已取消。' },
        ])
        setStreaming('')
      } else {
        setMessages((current) => [
          ...current,
          {
            id: crypto.randomUUID(),
            role: 'agent',
            content:
              terminal.code === 'agent_alpha_unavailable'
                ? '当前环境尚未完成 Agent Alpha 装配（可能是开关关闭、环境不匹配、Gate 未闭合、模型服务缺失或迁移版本不正确）。生产运行时仍保持锁定。'
                : `调用失败：${terminal.code}`,
          },
        ])
        setStreaming('')
      }
    } catch (error) {
      if (!guard.isCurrent(generation)) return
      // A user-initiated stop aborts the fetch (AbortError); never leak the
      // raw DOMException text.
      if (isUserCancelledError(error)) {
        setMessages((current) => [
          ...current,
          { id: crypto.randomUUID(), role: 'agent', content: '本次调用已取消。' },
        ])
        setStreaming('')
        return
      }
      const errorCode = error instanceof Error ? error.message : 'agent_alpha_failed'
      setMessages((current) => [
        ...current,
        {
          id: crypto.randomUUID(),
          role: 'agent',
          content:
            errorCode === 'agent_alpha_unavailable'
              ? '当前环境尚未完成 Agent Alpha 装配（可能是开关关闭、环境不匹配、Gate 未闭合、模型服务缺失或迁移版本不正确）。生产运行时仍保持锁定。'
              : `调用失败：${errorCode}`,
        },
      ])
      setStreaming('')
    } finally {
      // Only THIS generation and controller may settle the guard; a stale
      // invocation's finally can never clear a newer one.
      guard.settle(generation, controller)
      if (controllerRef.current === controller) controllerRef.current = null
      setPhase(guard.phase)
    }
  }

  const stop = async () => {
    const guard = guardRef.current!
    const controller = guard.stop()
    if (controller !== null) controllerRef.current = controller
    setPhase('cancelling')
    if (invocationId && workspaceId) {
      await agentAlphaApi.cancel(workspaceId, invocationId).catch(() => undefined)
    }
  }

  const postureBadges = (
    <div className="flex flex-wrap items-center gap-2">
      <Badge className="shrink-0" variant="outline">
        {personalRuntimeInvokeConditionsMet(posture)
          ? '个人版金丝雀已启用'
          : `轻量 Gate ${posture?.lite_gate_enabled ? '已开启' : '已关闭'}`}
      </Badge>

      <Badge className="shrink-0" variant="outline">
        <Wrench className="mr-1 h-3 w-3" />
        工具已禁用
      </Badge>
      <Badge className="shrink-0" variant="outline">
        {personalRuntimeInvokeConditionsMet(posture)
          ? '个人运行时已开启 · 无工具'
          : '生产运行时已关闭'}
      </Badge>
    </div>
  )

  return (
    <div className="grid h-[calc(100vh-7rem)] min-h-[640px] grid-cols-[minmax(0,1fr)_320px] gap-4">
      <section className="flex min-w-0 flex-col overflow-hidden rounded-2xl border bg-card shadow-sm">
        <header className="border-b px-6 py-5">
          <div className="flex items-center justify-between gap-4">
            <div>
              <div className="flex items-center gap-2">
                <Bot className="h-6 w-6 text-primary" />
                <h1 className="text-xl font-semibold">AI 员工工作台</h1>
              </div>
              <p className="mt-1 text-sm text-muted-foreground">
                管理已安装的 AI 员工、空间知识与可追溯的任务记录。
              </p>
            </div>
            <div className="flex items-center gap-3">
              {postureBadges}
              <Button size="sm" onClick={() => setBuilderOpen(true)} disabled={!workspaceId}>
                <UserPlus className="h-4 w-4" /> 新建员工
              </Button>
              <Button asChild variant="outline" size="sm">
                <Link href="/settings">
                  <Settings2 className="h-4 w-4" /> 模型设置
                </Link>
              </Button>
            </div>
          </div>
        </header>

        <div className="flex-1 space-y-5 overflow-y-auto px-6 py-6">
          {messages.length === 0 && !streaming && (
            <div className="flex h-full min-h-72 flex-col items-center justify-center text-center">
              <div className="mb-4 rounded-2xl bg-primary/10 p-4">
                <BrainCircuit className="h-10 w-10 text-primary" />
              </div>
              <h2 className="text-lg font-medium">
                {!workspaceId
                  ? '请先选择一个 AI 空间'
                  : !agentInvokeConditionsMet(posture)
                    ? '运行条件尚未满足'
                    : installations.length === 0
                      ? '当前空间尚未安装已封存的 AI 员工版本'
                      : '从这里开始使用你的 AI 员工'}
              </h2>
              <p className="mt-2 max-w-lg text-sm leading-6 text-muted-foreground">
                {!workspaceId
                  ? '请从右侧选择已有的 AI 空间。创建空间会使用正式的空间治理 API，不会绕过成员身份与权限范围。'
                  : !agentInvokeConditionsMet(posture)
                    ? '需要同时满足以下条件才能调用：轻量 Gate 已开启、当前环境已装配无工具版 Alpha、工程环境被允许，并且 Phase 5 的生产 Gate 保持关闭。生产运行时、规划器、多 Agent 与任意工具仍然锁定。'
                    : installations.length === 0
                      ? '点击“新建员工”创建一个 AI 员工，或安装一个已封存的员工版本。当前 Alpha 只能读取空间知识进行推理。'
                      : '请选择一个已封存并安装的员工版本。Alpha 可以读取空间知识进行推理，但不能执行工具、MCP、Shell、SQL 或任意 HTTP 请求。'}
              </p>
            </div>
          )}
          {messages.map((message) => (
            <article
              key={message.id}
              className={`max-w-[86%] rounded-2xl px-4 py-3 text-sm leading-6 ${
                message.role === 'user'
                  ? 'ml-auto bg-primary text-primary-foreground'
                  : 'border bg-muted/40'
              }`}
            >
              <div className="whitespace-pre-wrap">{message.content}</div>
              {message.role === 'agent' && message.citations && message.citations.length > 0 && (
                <div className="mt-3 border-t pt-2">
                    <p className="text-xs font-medium text-muted-foreground">引用来源</p>
                  <ul className="mt-1 space-y-1">
                    {message.citations.map((citation) => (
                      <li key={`${citation.index}-${citation.chunk_id}`} className="text-xs">
                        <span className="font-mono text-primary">[{citation.index}]</span>{' '}
                        <span className="text-muted-foreground">{citation.snippet}</span>{' '}
                        <span className="font-mono text-muted-foreground">
                          （片段 {citation.chunk_id.slice(0, 8)} · 文档{' '}
                          {citation.document_id.slice(0, 8)}）
                        </span>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </article>
          ))}
          {streaming && (
            <article className="max-w-[86%] rounded-2xl border bg-muted/40 px-4 py-3 text-sm leading-6">
              {streaming}
              <span className="ml-1 inline-block h-4 w-0.5 animate-pulse bg-primary align-middle" />
            </article>
          )}
        </div>

        <footer className="border-t p-4">
          <div className="flex items-center gap-2 rounded-xl border bg-background p-2 shadow-sm">
            <input
              value={input}
              onChange={(event) => setInput(event.target.value)}
              onKeyDown={(event) => event.key === 'Enter' && !event.shiftKey && invoke()}
              placeholder="让 AI 员工基于空间知识进行检索、解释或起草内容……"
              className="h-10 flex-1 bg-transparent px-3 text-sm outline-none placeholder:text-muted-foreground disabled:opacity-60"
              disabled={phase !== 'idle'}
            />
            {phase !== 'idle' ? (
              <Button variant="destructive" size="icon" onClick={stop} aria-label="停止调用">
                <Square className="h-4 w-4" />
              </Button>
            ) : (
              <Button
                size="icon"
                onClick={invoke}
                disabled={!canInvokeAgent(posture, input, workspaceId, bindingId)}
                aria-label="调用 AI 员工"
              >
                <Send className="h-4 w-4" />
              </Button>
            )}
          </div>
        </footer>
      </section>

      <aside className="space-y-4 overflow-y-auto">
        <section className="rounded-2xl border bg-card p-5 shadow-sm">
          <h2 className="text-sm font-semibold">调用目标</h2>
          <label className="mt-4 block text-xs font-medium text-muted-foreground">AI 空间</label>
          {workspaces.length === 0 ? (
            <p className="mt-2 rounded-md border border-dashed bg-muted/40 px-3 py-2 text-xs text-muted-foreground">
              暂无可用的 AI 空间。请先通过“AI 空间”页面创建；本工作台不会绕过成员身份或权限范围。
            </p>
          ) : (
            <Select value={workspaceId} onValueChange={setWorkspaceId}>
              <SelectTrigger className="mt-2">
                <SelectValue placeholder="选择一个 AI 空间" />
              </SelectTrigger>
              <SelectContent>
                {workspaces.map((workspace) => (
                  <SelectItem key={workspace.id} value={workspace.id}>
                    {workspace.display_name ?? workspace.id.slice(0, 8)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          )}
          <label className="mt-4 block text-xs font-medium text-muted-foreground">
            已安装的 AI 员工
          </label>
          {installations.length === 0 ? (
            <p className="mt-2 rounded-md border border-dashed bg-muted/40 px-3 py-2 text-xs text-muted-foreground">
              {workspaceId
                ? '这个空间尚未安装已封存的 AI 员工版本。'
                : '请先选择 AI 空间，再查看已安装的员工版本。'}
            </p>
          ) : (
            <Select value={bindingId} onValueChange={setBindingId}>
              <SelectTrigger className="mt-2">
                <SelectValue placeholder="选择已安装的员工版本" />
              </SelectTrigger>
              <SelectContent>
                {installations.map((item) => (
                  <SelectItem key={item.workspace_agent_binding_id} value={item.agent_version_id}>
                    {item.display_name} · 已封存 · 无工具
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          )}
          {selectedInstallation && (
            <div className="mt-4 space-y-1 text-xs text-muted-foreground">
              <p className="break-all">
                版本摘要：{' '}
                <span className="font-mono">
                  {selectedInstallation.agent_version_digest.slice(0, 16)}…
                </span>
              </p>
              <p>
                绑定标识：{' '}
                <span className="font-mono">
                  {selectedInstallation.workspace_agent_binding_id.slice(0, 12)}
                </span>
              </p>
              <p>配置：无工具 / 低风险 / 已封存</p>
            </div>
          )}
        </section>

        <section className="rounded-2xl border bg-card p-5 shadow-sm">
          <h2 className="text-sm font-semibold">空间能力</h2>
          <p className="mt-1 text-xs text-muted-foreground">
            当前工程版 Alpha 仅开放一个已安装的 AI 员工和只读空间知识；尚未接入的能力会明确标注。
          </p>
          <div className="mt-4 space-y-2 text-xs">
            <div className="flex items-center justify-between gap-2">
              <span className="text-muted-foreground">空间 / 员工版本选择</span>
              <Badge variant={workspaceId ? 'secondary' : 'outline'}>
                {workspaceId ? '可用' : '请选择'}
              </Badge>
            </div>
            <div className="flex items-center justify-between gap-2">
              <span className="text-muted-foreground">P5.4B 正式知识检索</span>
              <Badge variant="outline">
                {posture?.formal_builder_integration === 'proven_engineering_only'
                  ? '工程验证通过'
                  : '已锁定'}
              </Badge>
            </div>
            <div className="flex items-center justify-between gap-2">
              <span className="text-muted-foreground">项目 / 分支 / 文件</span>
              <Badge variant="outline">规划中</Badge>
            </div>
            <div className="flex items-center justify-between gap-2">
              <span className="text-muted-foreground">技能</span>
              <Badge variant="outline">规划中</Badge>
            </div>
            <div className="flex items-center justify-between gap-2">
              <span className="text-muted-foreground">MCP</span>
              <Badge variant="outline">已锁定</Badge>
            </div>
            <div className="flex items-center justify-between gap-2">
              <span className="text-muted-foreground">市场</span>
              <Badge variant="outline">规划中</Badge>
            </div>
          </div>
        </section>

        <section className="rounded-2xl border bg-card p-5 shadow-sm">
          <h2 className="text-sm font-semibold">运行状态</h2>
          <div className="mt-4 space-y-3 text-sm">
            <div className="flex items-start gap-3">
              <ShieldCheck className="mt-0.5 h-4 w-4 text-foreground" />
              <div>
                <p className="font-medium">仅限工程环境</p>
                <p className="text-xs text-muted-foreground">
                  {postureLoading
                    ? '正在读取实时状态……'
                    : (statusError
                      ? `暂时无法读取 Agent Alpha 状态（${statusError}）`
                      :
                      (!agentInvokeConditionsMet(posture)
                        ? '调用已锁定：必须满足完整的工程轻量运行条件，或存在精确匹配且有效的个人单所有者金丝雀。'
                        : personalRuntimeInvokeConditionsMet(posture)
                          ? `个人版金丝雀已启用${posture?.personal_canary_expires_at ? `，有效期至 ${posture.personal_canary_expires_at}` : ''}。仅允许无工具、单空间、单员工版本。`
                        : posture?.engineering_assembled
                            ? '当前环境已装配无工具版 Alpha。'
                            : '尚未装配；请检查模型服务、运行环境、Phase 5 Gate 与迁移版本。'))}
                </p>
              </div>
            </div>
            <div className="flex items-start gap-3">
              <Database className="mt-0.5 h-4 w-4 text-foreground" />
              <div>
                <p className="font-medium">正式知识检索构建器</p>
                <p className="text-xs text-muted-foreground">
                  {posture
                    ? `${posture.formal_builder}（${posture.formal_builder_integration}）— 仅限工程环境，生产环境不可选择`
                    : '选择 AI 空间后才能读取运行状态。'}
                </p>
                <p className="mt-1 break-all text-xs text-muted-foreground">
                  无工具调用链：{posture?.alpha_builder ?? 'build_engineering_agent_alpha'}。
                </p>
                <p className="mt-1 text-xs text-muted-foreground">
                  支持的调用模式：{posture?.supported_invocation_modes.join('、') ?? 'no_tool'}。
                </p>
              </div>
            </div>
            <div className="flex items-start gap-3">
              <Database className="mt-0.5 h-4 w-4 text-foreground" />
              <div>
                <p className="font-medium">持久任务记录</p>
                <p className="text-xs text-muted-foreground">
                  {taskId
                    ? `任务 ${taskId.slice(0, 12)}…`
                    : '记录任务、尝试、效果与用量边界。'}
                </p>
              </div>
            </div>
            <div className="flex items-start gap-3">
              {phase !== 'idle' ? (
                <Loader2 className="mt-0.5 h-4 w-4 animate-spin text-primary" />
              ) : (
                <BrainCircuit className="mt-0.5 h-4 w-4 text-foreground" />
              )}
              <div>
                <p className="font-medium">模型身份</p>
                <p className="break-all text-xs text-muted-foreground">{identity}</p>
              </div>
            </div>
            <div className="flex items-start gap-3">
              <BrainCircuit className="mt-0.5 h-4 w-4 text-muted-foreground" />
              <div>
                <p className="font-medium">用量 / 延迟</p>
                <p className="text-xs text-muted-foreground">
                  {usage
                    ? `输入 ${usage.input_tokens ?? 0} / 输出 ${usage.output_tokens ?? 0} / 合计 ${usage.total_tokens ?? 0} tokens`
                    : '尚无用量记录'}
                  {latencyMs !== null ? ` · ${latencyMs} ms` : ''}
                </p>
              </div>
            </div>
          </div>
        </section>
      </aside>

      <Dialog open={builderOpen} onOpenChange={setBuilderOpen}>
        <DialogContent className="max-h-[92vh] max-w-2xl overflow-y-auto border-foreground/20 bg-background sm:rounded-none">
          <DialogHeader>
            <DialogTitle>新建 AI 员工</DialogTitle>
            <DialogDescription>
              首个版本会被封存并限定在当前 AI 空间中，且严格禁止使用工具。它将使用已经测试通过的默认模型服务与只读空间知识。
            </DialogDescription>
          </DialogHeader>
          <div className="grid gap-5 py-2">
            <div className="grid gap-2">
              <Label htmlFor="agent-name">名称</Label>
              <Input
                id="agent-name"
                value={builder.displayName}
                onChange={(event) => setBuilder({ ...builder, displayName: event.target.value })}
                placeholder="例如：研究分析师"
              />
            </div>
            <div className="grid gap-2">
              <Label htmlFor="agent-role">角色与职责</Label>
              <textarea
                id="agent-role"
                value={builder.roleDescription}
                onChange={(event) =>
                  setBuilder({ ...builder, roleDescription: event.target.value })
                }
                className="min-h-24 border bg-background px-3 py-2 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring"
                placeholder="说明该员工负责什么、合格结果是什么，以及在什么情况下必须拒绝执行。"
              />
            </div>
            <div className="grid gap-2">
              <Label htmlFor="agent-instructions">系统指令</Label>
              <textarea
                id="agent-instructions"
                value={builder.instructions}
                onChange={(event) => setBuilder({ ...builder, instructions: event.target.value })}
                className="min-h-36 border bg-background px-3 py-2 font-mono text-xs leading-5 outline-none focus-visible:ring-2 focus-visible:ring-ring"
                placeholder="说明推理流程、输出结构、证据要求与拒绝条件。"
              />
            </div>
            <div className="grid gap-2">
              <Label htmlFor="agent-tone">回答风格</Label>
              <Input
                id="agent-tone"
                value={builder.assistantTone}
                onChange={(event) => setBuilder({ ...builder, assistantTone: event.target.value })}
              />
            </div>
            <div className="grid gap-3 border-y py-4 sm:grid-cols-3">
              <BuilderNumber
                label="上下文 tokens"
                value={builder.maxContextTokens}
                min={512}
                max={32_768}
                onChange={(value) => setBuilder({ ...builder, maxContextTokens: value })}
              />
              <BuilderNumber
                label="输出 tokens"
                value={builder.maxOutputTokens}
                min={64}
                max={8_192}
                onChange={(value) => setBuilder({ ...builder, maxOutputTokens: value })}
              />
              <BuilderNumber
                label="时限（秒）"
                value={builder.maxWallClockSeconds}
                min={1}
                max={300}
                onChange={(value) => setBuilder({ ...builder, maxWallClockSeconds: value })}
              />
            </div>
            <div className="grid gap-2 text-xs text-muted-foreground sm:grid-cols-3">
              <div className="border p-3">模型服务：用户默认配置</div>
              <div className="border p-3">知识：AI 空间只读</div>
              <div className="border p-3">工具 / 规划器 / 多 Agent：关闭</div>
            </div>
            {builderMessage && <div className="border px-3 py-2 text-sm">{builderMessage}</div>}
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setBuilderOpen(false)}>
              关闭
            </Button>
            <Button
              onClick={createAgent}
              disabled={
                builderSaving ||
                !workspaceId ||
                !builder.displayName.trim() ||
                !builder.roleDescription.trim() ||
                !builder.instructions.trim() ||
                !builder.assistantTone.trim()
              }
            >
              {builderSaving && <Loader2 className="h-4 w-4 animate-spin" />}
              封存并安装
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}

function BuilderNumber({
  label,
  value,
  min,
  max,
  onChange,
}: {
  label: string
  value: number
  min: number
  max: number
  onChange: (value: number) => void
}) {
  return (
    <div className="grid gap-2">
      <Label>{label}</Label>
      <Input
        type="number"
        value={value}
        min={min}
        max={max}
        onChange={(event) => onChange(Number(event.target.value))}
      />
    </div>
  )
}
