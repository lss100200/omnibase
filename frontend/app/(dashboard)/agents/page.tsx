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
import { canInvokeLiteAgent, liteInvokeConditionsMet } from '@/lib/lite-gate'
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
  const [identity, setIdentity] = useState('Provider identity appears after invocation')
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
    assistantTone: 'Professional, concise and explicit about uncertainty.',
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
      const createdMessage = `${result.definition.display_name} is sealed and installed.`
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
          `${createdMessage} Runtime profile refresh is unavailable; it will appear when Agent Alpha is assembled.`,
        )
      }
      setBuilder({
        displayName: '',
        roleDescription: '',
        instructions: '',
        assistantTone: 'Professional, concise and explicit about uncertainty.',
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
    if (!canInvokeLiteAgent(posture, userMessage, workspaceId, bindingId)) return
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
            content: terminal.answer || 'No answer returned.',
            citations: terminal.citations,
          },
        ])
        setStreaming('')
      } else if (terminal.kind === 'cancelled') {
        setMessages((current) => [
          ...current,
          { id: crypto.randomUUID(), role: 'agent', content: 'Invocation cancelled.' },
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
                ? 'Engineering Alpha is not assembled in this environment (flag off, wrong environment, gate open, missing provider or migration head not 0012). Production Runtime remains locked.'
                : `Invocation failed: ${terminal.code}`,
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
          { id: crypto.randomUUID(), role: 'agent', content: 'Invocation cancelled.' },
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
              ? 'Engineering Alpha is not assembled in this environment (flag off, wrong environment, gate open, missing provider or migration head not 0012). Production Runtime remains locked.'
              : `Invocation failed: ${errorCode}`,
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
        LITE GATE {posture?.lite_gate_enabled ? 'ON' : 'OFF'}
      </Badge>

      <Badge className="shrink-0" variant="outline">
        <Wrench className="mr-1 h-3 w-3" />
        TOOLS DISABLED
      </Badge>
      <Badge className="shrink-0" variant="outline">
        PRODUCTION RUNTIME OFF
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
                <h1 className="text-xl font-semibold">AI Employee Workbench</h1>
              </div>
              <p className="mt-1 text-sm text-muted-foreground">
                One installed Agent, workspace knowledge and a durable task ledger.
              </p>
            </div>
            <div className="flex items-center gap-3">
              {postureBadges}
              <Button size="sm" onClick={() => setBuilderOpen(true)} disabled={!workspaceId}>
                <UserPlus className="h-4 w-4" /> New employee
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
                  ? 'Select a Workspace to begin'
                  : !liteInvokeConditionsMet(posture)
                    ? 'Invocation is locked until every condition holds'
                    : installations.length === 0
                      ? 'No sealed AgentVersion installed'
                      : 'Your first AI employee starts here'}
              </h2>
              <p className="mt-2 max-w-lg text-sm leading-6 text-muted-foreground">
                {!workspaceId
                  ? 'Choose an existing Workspace from the right panel. Creating a Workspace uses the Workspace governance API; this engineering surface never bypasses membership or scope.'
                  : !liteInvokeConditionsMet(posture)
                    ? 'Invoke requires the Lite gate enabled, the tool-free Alpha assembled in this environment, an allowed engineering environment and all Phase 5 production gates false — simultaneously. Production Runtime, Planner, multi-Agent and arbitrary tools remain locked.'
                    : installations.length === 0
                      ? 'Create an Agent with "New employee", or ask your operator to seal and install an AgentVersion. Alpha can reason over read-only workspace knowledge only.'
                      : 'Select a sealed, installed AgentVersion. Alpha can reason over read-only workspace knowledge, but cannot execute tools, MCP, shell, SQL or arbitrary HTTP.'}
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
                  <p className="text-xs font-medium text-muted-foreground">Citations</p>
                  <ul className="mt-1 space-y-1">
                    {message.citations.map((citation) => (
                      <li key={`${citation.index}-${citation.chunk_id}`} className="text-xs">
                        <span className="font-mono text-primary">[{citation.index}]</span>{' '}
                        <span className="text-muted-foreground">{citation.snippet}</span>{' '}
                        <span className="font-mono text-muted-foreground">
                          (chunk {citation.chunk_id.slice(0, 8)} · doc{' '}
                          {citation.document_id.slice(0, 8)})
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
              placeholder="Ask your Agent to research, explain or draft from workspace knowledge..."
              className="h-10 flex-1 bg-transparent px-3 text-sm outline-none placeholder:text-muted-foreground disabled:opacity-60"
              disabled={phase !== 'idle'}
            />
            {phase !== 'idle' ? (
              <Button variant="destructive" size="icon" onClick={stop} aria-label="Stop invocation">
                <Square className="h-4 w-4" />
              </Button>
            ) : (
              <Button
                size="icon"
                onClick={invoke}
                disabled={!canInvokeLiteAgent(posture, input, workspaceId, bindingId)}
                aria-label="Invoke Agent"
              >
                <Send className="h-4 w-4" />
              </Button>
            )}
          </div>
        </footer>
      </section>

      <aside className="space-y-4 overflow-y-auto">
        <section className="rounded-2xl border bg-card p-5 shadow-sm">
          <h2 className="text-sm font-semibold">Invocation target</h2>
          <label className="mt-4 block text-xs font-medium text-muted-foreground">Workspace</label>
          {workspaces.length === 0 ? (
            <p className="mt-2 rounded-md border border-dashed bg-muted/40 px-3 py-2 text-xs text-muted-foreground">
              No Workspaces available. Create one via the Workspace governance API; this engineering
              surface never bypasses membership or scope.
            </p>
          ) : (
            <Select value={workspaceId} onValueChange={setWorkspaceId}>
              <SelectTrigger className="mt-2">
                <SelectValue placeholder="Select a workspace" />
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
            Installed Agent
          </label>
          {installations.length === 0 ? (
            <p className="mt-2 rounded-md border border-dashed bg-muted/40 px-3 py-2 text-xs text-muted-foreground">
              {workspaceId
                ? 'No sealed AgentVersion installed in this Workspace.'
                : 'Select a Workspace first to list installed AgentVersions.'}
            </p>
          ) : (
            <Select value={bindingId} onValueChange={setBindingId}>
              <SelectTrigger className="mt-2">
                <SelectValue placeholder="Select an installed AgentVersion" />
              </SelectTrigger>
              <SelectContent>
                {installations.map((item) => (
                  <SelectItem key={item.workspace_agent_binding_id} value={item.agent_version_id}>
                    {item.display_name} · sealed tool-free
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          )}
          {selectedInstallation && (
            <div className="mt-4 space-y-1 text-xs text-muted-foreground">
              <p className="break-all">
                Version digest:{' '}
                <span className="font-mono">
                  {selectedInstallation.agent_version_digest.slice(0, 16)}…
                </span>
              </p>
              <p>
                Binding:{' '}
                <span className="font-mono">
                  {selectedInstallation.workspace_agent_binding_id.slice(0, 12)}
                </span>
              </p>
              <p>Profile: tool-free / low risk / sealed</p>
            </div>
          )}
        </section>

        <section className="rounded-2xl border bg-card p-5 shadow-sm">
          <h2 className="text-sm font-semibold">Workspace surfaces</h2>
          <p className="mt-1 text-xs text-muted-foreground">
            Engineering-only Alpha exposes one installed Agent and read-only workspace knowledge.
            Surfaces not backed by current product state are labeled explicitly.
          </p>
          <div className="mt-4 space-y-2 text-xs">
            <div className="flex items-center justify-between gap-2">
              <span className="text-muted-foreground">Workspace / AgentVersion selection</span>
              <Badge variant={workspaceId ? 'secondary' : 'outline'}>
                {workspaceId ? 'LIVE' : 'SELECT'}
              </Badge>
            </div>
            <div className="flex items-center justify-between gap-2">
              <span className="text-muted-foreground">Formal P5.4B knowledge search</span>
              <Badge variant="outline">
                {posture?.formal_builder_integration === 'proven_engineering_only'
                  ? 'ENG-PROVEN'
                  : 'LOCKED'}
              </Badge>
            </div>
            <div className="flex items-center justify-between gap-2">
              <span className="text-muted-foreground">Projects / branches / files</span>
              <Badge variant="outline">ROADMAP</Badge>
            </div>
            <div className="flex items-center justify-between gap-2">
              <span className="text-muted-foreground">Skills</span>
              <Badge variant="outline">ROADMAP</Badge>
            </div>
            <div className="flex items-center justify-between gap-2">
              <span className="text-muted-foreground">MCP</span>
              <Badge variant="outline">LOCKED</Badge>
            </div>
            <div className="flex items-center justify-between gap-2">
              <span className="text-muted-foreground">Marketplace</span>
              <Badge variant="outline">ROADMAP</Badge>
            </div>
          </div>
        </section>

        <section className="rounded-2xl border bg-card p-5 shadow-sm">
          <h2 className="text-sm font-semibold">Runtime posture</h2>
          <div className="mt-4 space-y-3 text-sm">
            <div className="flex items-start gap-3">
              <ShieldCheck className="mt-0.5 h-4 w-4 text-foreground" />
              <div>
                <p className="font-medium">Engineering-only</p>
                <p className="text-xs text-muted-foreground">
                  {postureLoading
                    ? 'Reading live posture…'
                    : (statusError ??
                      (!liteInvokeConditionsMet(posture)
                        ? 'Invoke is locked: the Lite gate, the assembled engineering Alpha, the allowed environment and all-Phase-5-gates-false must hold simultaneously. Production Runtime remains locked.'
                        : posture?.engineering_assembled
                          ? 'Tool-free Alpha assembled in this environment.'
                          : 'Not assembled; check Provider, environment, Phase 5 gates and migration head 0012.'))}
                </p>
              </div>
            </div>
            <div className="flex items-start gap-3">
              <Database className="mt-0.5 h-4 w-4 text-foreground" />
              <div>
                <p className="font-medium">Formal knowledge-search builder</p>
                <p className="text-xs text-muted-foreground">
                  {posture
                    ? `${posture.formal_builder} (${posture.formal_builder_integration}) — engineering-only, not production-selectable`
                    : 'Posture unavailable until a Workspace is selected.'}
                </p>
                <p className="mt-1 break-all text-xs text-muted-foreground">
                  Tool-free loop: {posture?.alpha_builder ?? 'build_engineering_agent_alpha'}.
                </p>
                <p className="mt-1 text-xs text-muted-foreground">
                  Supported invocation modes:{' '}
                  {posture?.supported_invocation_modes.join(', ') ?? 'no_tool'}.
                </p>
              </div>
            </div>
            <div className="flex items-start gap-3">
              <Database className="mt-0.5 h-4 w-4 text-foreground" />
              <div>
                <p className="font-medium">Durable ledger</p>
                <p className="text-xs text-muted-foreground">
                  {taskId
                    ? `Task ${taskId.slice(0, 12)}…`
                    : 'Task, Attempt, Effect and usage boundaries.'}
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
                <p className="font-medium">Model identity</p>
                <p className="break-all text-xs text-muted-foreground">{identity}</p>
              </div>
            </div>
            <div className="flex items-start gap-3">
              <BrainCircuit className="mt-0.5 h-4 w-4 text-muted-foreground" />
              <div>
                <p className="font-medium">Usage / latency</p>
                <p className="text-xs text-muted-foreground">
                  {usage
                    ? `${usage.input_tokens ?? 0} in / ${usage.output_tokens ?? 0} out / ${usage.total_tokens ?? 0} total`
                    : 'No usage recorded yet'}
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
            <DialogTitle>Create an AI employee</DialogTitle>
            <DialogDescription>
              The first version is sealed, Workspace-scoped and strictly tool-free. It uses your
              tested default Provider and read-only Workspace knowledge.
            </DialogDescription>
          </DialogHeader>
          <div className="grid gap-5 py-2">
            <div className="grid gap-2">
              <Label htmlFor="agent-name">Name</Label>
              <Input
                id="agent-name"
                value={builder.displayName}
                onChange={(event) => setBuilder({ ...builder, displayName: event.target.value })}
                placeholder="Research analyst"
              />
            </div>
            <div className="grid gap-2">
              <Label htmlFor="agent-role">Role and responsibilities</Label>
              <textarea
                id="agent-role"
                value={builder.roleDescription}
                onChange={(event) =>
                  setBuilder({ ...builder, roleDescription: event.target.value })
                }
                className="min-h-24 border bg-background px-3 py-2 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring"
                placeholder="What this employee owns, what good work looks like, and when it should say no."
              />
            </div>
            <div className="grid gap-2">
              <Label htmlFor="agent-instructions">System instructions</Label>
              <textarea
                id="agent-instructions"
                value={builder.instructions}
                onChange={(event) => setBuilder({ ...builder, instructions: event.target.value })}
                className="min-h-36 border bg-background px-3 py-2 font-mono text-xs leading-5 outline-none focus-visible:ring-2 focus-visible:ring-ring"
                placeholder="Describe the reasoning process, output structure, evidence requirements and refusal conditions."
              />
            </div>
            <div className="grid gap-2">
              <Label htmlFor="agent-tone">Answer style</Label>
              <Input
                id="agent-tone"
                value={builder.assistantTone}
                onChange={(event) => setBuilder({ ...builder, assistantTone: event.target.value })}
              />
            </div>
            <div className="grid gap-3 border-y py-4 sm:grid-cols-3">
              <BuilderNumber
                label="Context tokens"
                value={builder.maxContextTokens}
                min={512}
                max={32_768}
                onChange={(value) => setBuilder({ ...builder, maxContextTokens: value })}
              />
              <BuilderNumber
                label="Output tokens"
                value={builder.maxOutputTokens}
                min={64}
                max={8_192}
                onChange={(value) => setBuilder({ ...builder, maxOutputTokens: value })}
              />
              <BuilderNumber
                label="Deadline (seconds)"
                value={builder.maxWallClockSeconds}
                min={1}
                max={300}
                onChange={(value) => setBuilder({ ...builder, maxWallClockSeconds: value })}
              />
            </div>
            <div className="grid gap-2 text-xs text-muted-foreground sm:grid-cols-3">
              <div className="border p-3">Provider: user default</div>
              <div className="border p-3">Knowledge: Workspace read-only</div>
              <div className="border p-3">Tools / Planner / multi-Agent: off</div>
            </div>
            {builderMessage && <div className="border px-3 py-2 text-sm">{builderMessage}</div>}
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setBuilderOpen(false)}>
              Close
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
              Seal and install
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
