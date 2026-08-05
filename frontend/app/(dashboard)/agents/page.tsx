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

interface ParsedEvent {
  readonly event: string
  readonly data: Record<string, unknown>
}

interface Citation {
  readonly index: number
  readonly chunk_id: string
  readonly document_id: string
  readonly snippet: string
  readonly page_number?: number
  readonly score?: number
}

interface UsageInfo {
  readonly input_tokens?: number
  readonly output_tokens?: number
  readonly total_tokens?: number
}

function parseEvents(buffer: string): [ParsedEvent[], string] {
  const blocks = buffer.replaceAll('\r\n', '\n').split('\n\n')
  const remaining = blocks.pop() ?? ''
  const events: ParsedEvent[] = []
  for (const block of blocks) {
    let event = 'message'
    const dataLines: string[] = []
    for (const line of block.split('\n')) {
      if (line.startsWith('event:')) event = line.slice(6).trim()
      if (line.startsWith('data:')) dataLines.push(line.slice(5).trim())
    }
    if (dataLines.length > 0) {
      events.push({ event, data: JSON.parse(dataLines.join('\n')) as Record<string, unknown> })
    }
  }
  return [events, remaining]
}

export default function AgentAlphaPage() {
  const [workspaces, setWorkspaces] = useState<Array<{ id: string; display_name: string }>>([])
  const [workspaceId, setWorkspaceId] = useState('')
  const [installations, setInstallations] = useState<AgentAlphaProfile[]>([])
  const [bindingId, setBindingId] = useState('')
  const [input, setInput] = useState('')
  const [messages, setMessages] = useState<WorkbenchMessage[]>([])
  const [streaming, setStreaming] = useState('')
  const [running, setRunning] = useState(false)
  const [invocationId, setInvocationId] = useState<string | null>(null)
  const [taskId, setTaskId] = useState<string | null>(null)
  const [identity, setIdentity] = useState('Provider identity appears after invocation')
  const [usage, setUsage] = useState<UsageInfo | null>(null)
  const [latencyMs, setLatencyMs] = useState<number | null>(null)
  const [posture, setPosture] = useState<{
    engineering_assembled: boolean
    engineering_flag_enabled: boolean
    environment_allowed: boolean
    phase5_gates_all_false: boolean
    production_activation_allowed: boolean
    tools_enabled: boolean
    multi_agent_enabled: boolean
  } | null>(null)
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
      .then((status) => setPosture(status))
      .catch(() => setStatusError('agent_alpha_unavailable'))
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
    if (!userMessage || !workspaceId || !bindingId || running) return
    setMessages((current) => [
      ...current,
      { id: crypto.randomUUID(), role: 'user', content: userMessage },
    ])
    setInput('')
    setStreaming('')
    setUsage(null)
    setLatencyMs(null)
    setTaskId(null)
    setRunning(true)
    const controller = new AbortController()
    controllerRef.current = controller
    startedAtRef.current = performance.now()
    let answer = ''
    let citations: Citation[] = []
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
      const reader = response.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''
      let terminal = false
      while (!terminal) {
        const { done, value } = await reader.read()
        buffer += done ? decoder.decode() : decoder.decode(value, { stream: true })
        const [events, remaining] = parseEvents(buffer)
        buffer = remaining
        for (const event of events) {
          if (event.event === 'meta') {
            const currentInvocation = String(event.data.invocation_id ?? '')
            setInvocationId(currentInvocation || null)
            setTaskId(String(event.data.task_id ?? '') || null)
            setIdentity(
              `${String(event.data.provider_id ?? 'unknown')} / ${String(
                event.data.requested_model_id ?? 'unknown',
              )} · ${String(event.data.credential_source ?? 'operator_default')}`,
            )
          } else if (event.event === 'citations') {
            citations = Array.isArray(event.data.citations)
              ? (event.data.citations as Citation[])
              : []
          } else if (event.event === 'chunk') {
            answer += String(event.data.content ?? '')
            setStreaming(answer)
          } else if (event.event === 'usage') {
            setUsage({
              input_tokens: Number(event.data.input_tokens ?? 0),
              output_tokens: Number(event.data.output_tokens ?? 0),
              total_tokens: Number(event.data.total_tokens ?? 0),
            })
          } else if (event.event === 'done') {
            answer = String(event.data.answer ?? answer)
            const actualModel = String(event.data.actual_model_id ?? '')
            if (actualModel) {
              setIdentity(
                `${String(event.data.provider_id ?? 'unknown')} / ${actualModel} (actual) · ${String(
                  event.data.credential_source ?? 'operator_default',
                )}`,
              )
            }
            setUsage(
              typeof event.data.usage === 'object' && event.data.usage !== null
                ? (event.data.usage as UsageInfo)
                : null,
            )
            terminal = true
          } else if (event.event === 'error' || event.event === 'cancelled') {
            throw new Error(String(event.data.code ?? event.event))
          }
        }
        if (done) break
      }
      if (startedAtRef.current !== null) {
        setLatencyMs(Math.round(performance.now() - startedAtRef.current))
      }
      setMessages((current) => [
        ...current,
        {
          id: crypto.randomUUID(),
          role: 'agent',
          content: answer || 'No answer returned.',
          citations,
        },
      ])
      setStreaming('')
    } catch (error) {
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
      setRunning(false)
      controllerRef.current = null
    }
  }

  const stop = async () => {
    controllerRef.current?.abort()
    if (invocationId && workspaceId) {
      await agentAlphaApi.cancel(workspaceId, invocationId).catch(() => undefined)
    }
    setRunning(false)
  }

  const postureBadges = (
    <div className="flex flex-wrap items-center gap-2">
      <Badge className="shrink-0" variant="secondary">
        ENGINEERING ALPHA
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
              <h2 className="text-lg font-medium">Your first AI employee starts here</h2>
              <p className="mt-2 max-w-lg text-sm leading-6 text-muted-foreground">
                Select a Workspace and a sealed, installed AgentVersion. Alpha can reason over
                read-only workspace knowledge, but cannot execute tools, MCP, shell, SQL or
                arbitrary HTTP.
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
              disabled={running}
            />
            {running ? (
              <Button variant="destructive" size="icon" onClick={stop} aria-label="Stop invocation">
                <Square className="h-4 w-4" />
              </Button>
            ) : (
              <Button
                size="icon"
                onClick={invoke}
                disabled={!input.trim() || !workspaceId || !bindingId}
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
          <label className="mt-4 block text-xs font-medium text-muted-foreground">
            Installed Agent
          </label>
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
          <h2 className="text-sm font-semibold">Runtime posture</h2>
          <div className="mt-4 space-y-3 text-sm">
            <div className="flex items-start gap-3">
              <ShieldCheck className="mt-0.5 h-4 w-4 text-foreground" />
              <div>
                <p className="font-medium">Engineering-only</p>
                <p className="text-xs text-muted-foreground">
                  {statusError ??
                    (posture?.engineering_assembled
                      ? 'Assembled in this environment.'
                      : 'Not assembled; production remains locked.')}
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
              {running ? (
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
