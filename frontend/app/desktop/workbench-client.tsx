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
  beginDesktopLiveSend,
  completeDesktopLiveSend,
  createDesktopLiveStreamState,
  desktopInvocationCancelTarget,
  desktopInvocationIsStopping,
  desktopInvocationLiveProjection,
  desktopInvocationNeedsStreamAbort,
  desktopLiveSendBlocked,
  desktopLiveStopVisible,
  markDesktopInvocationCancelDispatched,
  reduceDesktopInvocationEvent,
  requestDesktopLiveCancel,
  switchDesktopLiveScope,
  beginDesktopTeamRun,
  createDesktopTeamLiveState,
  desktopTeamLiveProjection,
  desktopTeamStopVisible,
  projectDesktopTeamBudget,
  projectDesktopTeamEmployees,
  projectDesktopTeamTimeline,
  reduceDesktopTeamEvent,
  requestDesktopTeamCancel,
  switchDesktopTeamScope,
  type DesktopReasoningGear,
  type DesktopThinkingDepth,
} from '@/lib/desktop-bridge'
import { parseEmployeeInvocation, prepareEmployeeRoleMessage } from '@/lib/p6-workbench'
import {
  applyDesktopConversationArchive,
  applyDesktopConversationCompletion,
  applyDesktopConversationCreate,
  applyDesktopConversationDetail,
  applyDesktopSurfaceError,
  applyDesktopWorkspaceLoad,
  beginDesktopSurfaceDetailRequest,
  beginDesktopSurfaceMutation,
  beginDesktopSurfaceWorkspaceLoad,
  createDesktopConversationSurface,
  selectDesktopConversation,
  unmountDesktopConversationSurface,
} from '@/lib/desktop-conversation-surface'
import {
  advanceDesktopSurfaceScope,
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

const TEAM_SPECIALISTS = [
  'product',
  'ux',
  'frontend',
  'backend',
  'data',
  'security',
  'qa',
  'operations',
  'docs',
] as const

const DEFAULT_TEAM_BUDGET = {
  maximumProviderCalls: 16,
  maximumWallTimeMs: 600_000,
  maximumConcurrentCalls: 3,
  maximumInputCharacters: 16_384,
  maximumOutputCharacters: 32_768,
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
  const [messagesStatus, setMessagesStatus] = useState<'empty' | 'loading' | 'ready' | 'error'>('empty')
  const [messagesError, setMessagesError] = useState<string | null>(null)
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
  const [teamLive, setTeamLive] = useState(() =>
    createDesktopTeamLiveState({
      workspaceId: initialWorkspaceId,
      conversationId: null,
    }),
  )
  const teamLiveRef = useRef(teamLive)
  const [teamMode, setTeamMode] = useState(false)
  const [allowedSpecialists, setAllowedSpecialists] = useState<readonly string[]>([...TEAM_SPECIALISTS])
  const [teamBudget, setTeamBudget] = useState(DEFAULT_TEAM_BUDGET)
  const [appendCalls, setAppendCalls] = useState('20')
  const rosterEpochRef = useRef(1)
  const surfaceScopeRef = useRef<DesktopSurfaceScope>(
    createDesktopSurfaceScope(initialWorkspaceId, null),
  )
  const conversationSurfaceRef = useRef(
    createDesktopConversationSurface<DesktopMessage, DesktopConversation>(initialWorkspaceId, null),
  )
  const mountedRef = useRef(true)

  const applyViewScope = useCallback((nextWorkspaceId: string | null, nextConversationId: string | null) => {
    const next = advanceDesktopSurfaceScope(
      surfaceScopeRef.current,
      nextWorkspaceId,
      nextConversationId,
    )
    surfaceScopeRef.current = next
    setWorkspaceId(next.workspaceId)
    setConversationId(next.conversationId)
    const nextLive = switchDesktopLiveScope(liveRef.current, next.workspaceId, next.conversationId)
    liveRef.current = nextLive
    setLive(nextLive)
    const nextTeam = switchDesktopTeamScope(teamLiveRef.current, next.workspaceId, next.conversationId)
    teamLiveRef.current = nextTeam
    setTeamLive(nextTeam)
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
  const liveProjection = desktopInvocationLiveProjection(live, workspaceId, conversationId)
  const teamProjection = desktopTeamLiveProjection(teamLive, workspaceId, conversationId)
  const sendBlocked = desktopLiveSendBlocked(live) || desktopTeamStopVisible(teamLive)
  const stopping = desktopInvocationIsStopping(live) || teamLive.phase === 'cancelling'

  const loadWorkspaceSurface = useCallback(
    async (nextWorkspaceId: string) => {
      const startedScope = surfaceScopeRef.current
      const load = beginDesktopSurfaceWorkspaceLoad(conversationSurfaceRef.current)
      conversationSurfaceRef.current = load.surface
      const [conversationResult, providerResult, agentResult] = await Promise.all([
        bridge.conversations.list({ workspaceId: nextWorkspaceId }),
        bridge.providers.list(),
        bridge.workspaces.agent({ workspaceId: nextWorkspaceId }),
      ])
      if (!mountedRef.current) return
      if (load.epoch !== conversationSurfaceRef.current.workspaceLoadEpoch) return
      if (!desktopSurfaceProjectionIsCurrent(startedScope, surfaceScopeRef.current)) return
      if (!conversationResult.ok) {
        if (
          conversationSurfaceRef.current.workspaceId === nextWorkspaceId &&
          applyDesktopSurfaceError(conversationSurfaceRef.current, load.epoch, 'workspace')
        ) {
          onError(errorMessage(conversationResult.error.code))
        }
        return
      }
      if (!providerResult.ok) {
        if (
          conversationSurfaceRef.current.workspaceId === nextWorkspaceId &&
          applyDesktopSurfaceError(conversationSurfaceRef.current, load.epoch, 'workspace')
        ) {
          onError(errorMessage(providerResult.error.code))
        }
        return
      }
      if (agentResult.ok) setAgentName(agentResult.value.agent.displayName)
      setProviders(providerResult.value.items)
      const active = conversationResult.value.items.filter((item) => item.state === 'active')
      const selected =
        conversationSurfaceRef.current.conversationId !== null
          ? active.find((item) => item.id === conversationSurfaceRef.current.conversationId) ?? active[0]
          : active[0]
      const loaded = applyDesktopWorkspaceLoad(
        conversationSurfaceRef.current,
        { epoch: load.epoch, workspaceId: nextWorkspaceId },
        conversationResult.value.items,
        selected?.id ?? null,
      )
      conversationSurfaceRef.current = loaded
      setConversations(loaded.conversations)
      if (loaded.conversationId !== surfaceScopeRef.current.conversationId) {
        applyViewScope(nextWorkspaceId, loaded.conversationId)
      }
      if (selected === undefined) {
        setMessages([])
        setMessagesStatus('empty')
        setMessagesError(null)
        return
      }
      if (loaded.conversationId !== selected.id) {
        return
      }
      const detailReq = beginDesktopSurfaceDetailRequest(conversationSurfaceRef.current)
      conversationSurfaceRef.current = detailReq.surface
      setMessages([])
      setMessagesStatus('loading')
      setMessagesError(null)
      const detail = await bridge.conversations.get({
        workspaceId: nextWorkspaceId,
        conversationId: selected.id,
      })
      const applied = applyDesktopConversationDetail(
        conversationSurfaceRef.current,
        detailReq.epoch,
        selected.id,
        detail.ok
          ? { ok: true, messages: detail.value.messages }
          : { ok: false, error: errorMessage(detail.error.code) },
      )
      conversationSurfaceRef.current = applied
      if (!applied.mounted || applied.detailRequestEpoch !== detailReq.epoch) return
      if (applied.conversationId !== selected.id) return
      setMessages(applied.messages)
      setMessagesStatus(applied.messagesStatus)
      setMessagesError(applied.messagesError)
      if (applied.messagesStatus === 'error' && applied.messagesError !== null) {
        onError(applied.messagesError)
      }
    },
    [applyViewScope, bridge, onError],
  )

  useEffect(() => {
    mountedRef.current = true
    conversationSurfaceRef.current = { ...conversationSurfaceRef.current, mounted: true }
    return () => {
      mountedRef.current = false
      conversationSurfaceRef.current = unmountDesktopConversationSurface(conversationSurfaceRef.current)
    }
  }, [])

  useEffect(() => {
    if (workspaceId !== null) void loadWorkspaceSurface(workspaceId)
  }, [loadWorkspaceSurface, workspaceId])

  useEffect(() => {
    setLive((current) => {
      const next = switchDesktopLiveScope(current, workspaceId, conversationId)
      liveRef.current = next
      return next
    })
    setTeamLive((current) => {
      const next = switchDesktopTeamScope(current, workspaceId, conversationId)
      teamLiveRef.current = next
      return next
    })
  }, [conversationId, workspaceId])

  useEffect(() => {
    return bridge.conversations.subscribe((event) => {
      const current = liveRef.current
      const reduced = reduceDesktopInvocationEvent(current, event)
      liveRef.current = reduced.state
      setLive(reduced.state)
      if (reduced.cancelInvocationId !== null) {
        void bridge.conversations.cancel({ invocationId: reduced.cancelInvocationId })
      }
      if (
        mountedRef.current &&
        event.type === 'cancelled' &&
        current.invocationId !== null &&
        event.invocationId === current.invocationId
      ) {
        onError('生成已停止')
      }
    })
  }, [bridge, onError])

  useEffect(() => {
    return bridge.teamRuns.subscribe((event) => {
      const next = reduceDesktopTeamEvent(teamLiveRef.current, event)
      teamLiveRef.current = next
      setTeamLive(next)
    })
  }, [bridge])

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ block: 'end' })
  }, [messages, live.liveText, teamLive.parentLiveText, teamLive.parentFinalAnswer])

  const createConversation = async (): Promise<string | null> => {
    if (workspaceId === null) return null
    const startedScope = surfaceScopeRef.current
    const startedWorkspaceId = workspaceId
    const mutation = beginDesktopSurfaceMutation(conversationSurfaceRef.current)
    conversationSurfaceRef.current = mutation.surface
    const created = await bridge.conversations.create({ workspaceId: startedWorkspaceId })
    if (!mountedRef.current) return null
    if (!created.ok) {
      if (
        conversationSurfaceRef.current.workspaceId === mutation.workspaceId &&
        conversationSurfaceRef.current.listGeneration === mutation.listGeneration &&
        applyDesktopSurfaceError(conversationSurfaceRef.current, mutation.epoch, 'mutation')
      ) {
        onError(errorMessage(created.error.code))
      }
      return null
    }
    const applied = applyDesktopConversationCreate(
      conversationSurfaceRef.current,
      mutation,
      created.value.conversation,
    )
    conversationSurfaceRef.current = applied
    setConversations(applied.conversations)
    if (!desktopSurfaceProjectionIsCurrent(startedScope, surfaceScopeRef.current)) return null
    if (applied.workspaceId !== startedWorkspaceId) return null
    const selected = selectDesktopConversation(
      conversationSurfaceRef.current,
      startedWorkspaceId,
      created.value.conversation.id,
    )
    conversationSurfaceRef.current = selected
    applyViewScope(startedWorkspaceId, created.value.conversation.id)
    setMessages([])
    setMessagesStatus('empty')
    setMessagesError(null)
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
    if (!mountedRef.current) return
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
    conversationSurfaceRef.current = selectDesktopConversation(
      conversationSurfaceRef.current,
      result.value.workspace.id,
      null,
    )
    setConversations([])
    setMessages([])
    setMessagesStatus('empty')
    setMessagesError(null)
    setWorkspaceName('')
  }

  const archiveCurrentConversation = async () => {
    const current = conversations.find((item) => item.id === conversationId)
    if (workspaceId === null || current === undefined) return
    const archivedId = current.id
    const mutation = beginDesktopSurfaceMutation(conversationSurfaceRef.current)
    conversationSurfaceRef.current = mutation.surface
    const result = await bridge.conversations.archive({
      workspaceId,
      conversationId: archivedId,
      expectedRowVersion: current.rowVersion,
    })
    if (!mountedRef.current) return
    if (!result.ok) {
      if (
        conversationSurfaceRef.current.workspaceId === mutation.workspaceId &&
        conversationSurfaceRef.current.listGeneration === mutation.listGeneration &&
        applyDesktopSurfaceError(conversationSurfaceRef.current, mutation.epoch, 'mutation')
      ) {
        onError(errorMessage(result.error.code))
      }
      return
    }
    const updated = conversationSurfaceRef.current.conversations.map((item) =>
      item.id === result.value.conversation.id ? result.value.conversation : item,
    )
    const remaining = updated.filter((item) => item.id !== archivedId && item.state === 'active')
    const applied = applyDesktopConversationArchive(
      conversationSurfaceRef.current,
      mutation,
      archivedId,
      updated,
      remaining[0]?.id ?? null,
    )
    conversationSurfaceRef.current = applied
    setConversations(applied.conversations)
    if (applied.conversationId === archivedId) return
    if (applied.conversationId === surfaceScopeRef.current.conversationId) return
    applyViewScope(workspaceId, applied.conversationId)
    setMessages(applied.messages)
    setMessagesStatus(applied.messagesStatus)
    setMessagesError(applied.messagesError)
    if (applied.conversationId === null) return
    const detailReq = beginDesktopSurfaceDetailRequest(conversationSurfaceRef.current)
    conversationSurfaceRef.current = detailReq.surface
    const nextId = applied.conversationId
    void bridge.conversations.get({ workspaceId, conversationId: nextId }).then((detail) => {
      const next = applyDesktopConversationDetail(
        conversationSurfaceRef.current,
        detailReq.epoch,
        nextId,
        detail.ok
          ? { ok: true, messages: detail.value.messages }
          : { ok: false, error: errorMessage(detail.error.code) },
      )
      conversationSurfaceRef.current = next
      if (!next.mounted || next.detailRequestEpoch !== detailReq.epoch) return
      if (next.conversationId !== nextId) return
      setMessages(next.messages)
      setMessagesStatus(next.messagesStatus)
      setMessagesError(next.messagesError)
    })
  }

  const sendMessage = async (event: FormEvent) => {
    event.preventDefault()
    const content = draft.trim()
    if (workspaceId === null || content === '' || desktopLiveSendBlocked(live) || desktopLiveSendBlocked(liveRef.current) || desktopTeamStopVisible(teamLiveRef.current)) return
    const routed = parseEmployeeInvocation(content)
    if (!routed.ok) {
      onError(routed.message)
      return
    }
    const target = await ensureConversation()
    if (target === null) return
    if (teamMode && !routed.explicitMention) {
      onError(null)
      setDraft('')
      const rosterEpoch = rosterEpochRef.current
      rosterEpochRef.current += 1
      const started = beginDesktopTeamRun(teamLiveRef.current, {
        workspaceId,
        conversationId: target,
        rosterEpoch,
        maximumProviderCalls: teamBudget.maximumProviderCalls,
      })
      teamLiveRef.current = started
      setTeamLive(started)
      const allowed =
        allowedSpecialists.length === TEAM_SPECIALISTS.length
          ? undefined
          : allowedSpecialists
      const result = await bridge.teamRuns.execute({
        workspaceId,
        conversationId: target,
        task: routed.message,
        teamMode: true,
        rosterEpoch,
        budget: teamBudget,
        ...(allowed === undefined ? {} : { allowedSpecialistRoleIds: allowed }),
      })
      if (!mountedRef.current) return
      if (!result.ok) {
        onError(errorMessage(result.error.code))
        return
      }
      if (result.value.proof.state === 'budget_exhausted') {
        onError('团队已经使用完本次协作预算；未伪造完成。')
      }
      return
    }
    const prepared = prepareEmployeeRoleMessage(routed.employee, routed.message)
    if (!prepared.ok) {
      onError(prepared.code === 'message_too_long' ? '消息过长。' : '无法发送。')
      return
    }
    const completion = beginDesktopSurfaceDetailRequest(conversationSurfaceRef.current)
    conversationSurfaceRef.current = completion.surface
    onError(null)
    setDraft('')
    const nextLive = beginDesktopLiveSend({
      ...liveRef.current,
      workspaceId,
      conversationId: target,
    })
    if (nextLive.phase !== 'starting_identity') return
    liveRef.current = nextLive
    setLive(nextLive)
    const result = await bridge.conversations.send({
      workspaceId,
      conversationId: target,
      content: prepared.roleMessage,
      sendEpoch: nextLive.sendEpoch,
    })
    const completed = completeDesktopLiveSend(liveRef.current, nextLive.sendGeneration)
    liveRef.current = completed
    setLive(completed)
    if (!mountedRef.current) return
    if (completed.phase === 'idle' && (completed.terminalStatus === 'cancelled' || (result.ok && result.value.type === 'cancelled'))) {
      onError('生成已停止')
    }
    if (!result.ok) {
      if (completed.terminalStatus === 'cancelled') {
        onError('生成已停止')
      } else if (applyDesktopSurfaceError(conversationSurfaceRef.current, completion.epoch, 'detail')) {
        onError(errorMessage(result.error.code))
      }
      return
    }
    const detail = await bridge.conversations.get({
      workspaceId,
      conversationId: target,
    })
    if (!mountedRef.current) return
    if (!detail.ok) return
    const updatedConversations = conversationSurfaceRef.current.conversations.map((item) =>
      item.id === detail.value.conversation.id ? detail.value.conversation : item,
    )
    const applied = applyDesktopConversationCompletion(
      conversationSurfaceRef.current,
      completion.epoch,
      target,
      detail.value.messages,
      updatedConversations,
    )
    conversationSurfaceRef.current = applied
    if (!applied.mounted || applied.detailRequestEpoch !== completion.epoch) return
    if (applied.conversationId !== target) return
    setMessages(applied.messages)
    setMessagesStatus(applied.messagesStatus)
    setMessagesError(null)
    setConversations(applied.conversations)
  }

  const stopGeneration = async () => {
    const current = liveRef.current
    const teamCurrent = teamLiveRef.current
    const teamStop = desktopTeamStopVisible(teamCurrent)
    if (!desktopLiveStopVisible(current) && current.invocationId === null && !teamStop) return
    if (teamStop) {
      const cancelledTeam = requestDesktopTeamCancel(teamCurrent)
      teamLiveRef.current = cancelledTeam
      setTeamLive(cancelledTeam)
      if (mountedRef.current) onError('正在停止')
      await bridge.conversations.abortInFlightSend()
      if (cancelledTeam.teamRunId !== null) {
        await bridge.teamRuns.cancel({
          workspaceId: cancelledTeam.originWorkspaceId ?? workspaceId ?? '',
          teamRunId: cancelledTeam.teamRunId,
        })
      }
    }
    if (!desktopLiveStopVisible(current) && current.invocationId === null) return
    let cancelled = requestDesktopLiveCancel(current)
    const abortStream = desktopInvocationNeedsStreamAbort(cancelled)
    const target = desktopInvocationCancelTarget(cancelled)
    if (target !== null) {
      cancelled = markDesktopInvocationCancelDispatched(cancelled)
    }
    liveRef.current = cancelled
    setLive(cancelled)
    if (mountedRef.current) onError('正在停止')
    if (abortStream) {
      const abortResult = await bridge.conversations.abortInFlightSend()
      if (!abortResult.ok || !abortResult.value.aborted) {
        queueMicrotask(() => {
          if (!mountedRef.current) return
          if (!desktopInvocationNeedsStreamAbort(liveRef.current)) return
          void bridge.conversations.abortInFlightSend()
        })
      }
    }
    if (target !== null) {
      await bridge.conversations.cancel({ invocationId: target })
    }
  }

  const retryLast = async () => {
    if (workspaceId === null || conversationId === null || desktopLiveSendBlocked(live) || desktopLiveSendBlocked(liveRef.current)) return
    const failed = [...messages].reverse().find((item) => item.role === 'assistant' && item.status !== 'completed')
    if (failed === undefined) return
    const target = conversationId
    const completion = beginDesktopSurfaceDetailRequest(conversationSurfaceRef.current)
    conversationSurfaceRef.current = completion.surface
    const nextLive = beginDesktopLiveSend(liveRef.current)
    if (nextLive.phase !== 'starting_identity') return
    liveRef.current = nextLive
    setLive(nextLive)
    const result = await bridge.conversations.send({
      workspaceId,
      conversationId: target,
      content: '',
      retryOfMessageId: failed.id,
      sendEpoch: nextLive.sendEpoch,
    })
    const completed = completeDesktopLiveSend(liveRef.current, nextLive.sendGeneration)
    liveRef.current = completed
    setLive(completed)
    if (!mountedRef.current) return
    if (completed.phase === 'idle' && (completed.terminalStatus === 'cancelled' || (result.ok && result.value.type === 'cancelled'))) {
      onError('生成已停止')
    }
    if (!result.ok) {
      if (completed.terminalStatus === 'cancelled') {
        onError('生成已停止')
      } else if (applyDesktopSurfaceError(conversationSurfaceRef.current, completion.epoch, 'detail')) {
        onError(errorMessage(result.error.code))
      }
      return
    }
    const detail = await bridge.conversations.get({ workspaceId, conversationId: target })
    if (!mountedRef.current || !detail.ok) return
    const applied = applyDesktopConversationCompletion(
      conversationSurfaceRef.current,
      completion.epoch,
      target,
      detail.value.messages,
    )
    conversationSurfaceRef.current = applied
    if (!applied.mounted || applied.detailRequestEpoch !== completion.epoch) return
    if (applied.conversationId !== target) return
    setMessages(applied.messages)
    setMessagesStatus(applied.messagesStatus)
    setMessagesError(null)
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
      if (!mountedRef.current) return
      if (!result.ok) {
        onError(errorMessage(result.error.code))
        return
      }
      const listed = await bridge.providers.list()
      if (!mountedRef.current) return
      if (listed.ok) setProviders(listed.value.items)
      setProviderForm((current) => ({ ...current, apiKey: '' }))
      setTestResult('Provider 已保存。API Key 不会回读到界面。')
    } finally {
      setSubmitting(false)
    }
  }

  const testSelected = async (providerId: string) => {
    const result = await bridge.providers.test({ providerId })
    if (!mountedRef.current) return
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
            {stopping && (
              <Badge variant="outline" className="rounded-none">
                正在停止
              </Badge>
            )}
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
                    conversationSurfaceRef.current = selectDesktopConversation(
                      conversationSurfaceRef.current,
                      workspace.id,
                      null,
                    )
                    setConversations([])
                    setMessages([])
                    setMessagesStatus('empty')
                    setMessagesError(null)
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
                      if (workspaceId === null) return
                      applyViewScope(workspaceId, item.id)
                      const selected = selectDesktopConversation(
                        conversationSurfaceRef.current,
                        workspaceId,
                        item.id,
                      )
                      conversationSurfaceRef.current = selected
                      setMessages([])
                      setMessagesStatus('loading')
                      setMessagesError(null)
                      const epoch = selected.detailRequestEpoch
                      void bridge.conversations
                        .get({ workspaceId, conversationId: item.id })
                        .then((detail) => {
                          const applied = applyDesktopConversationDetail(
                            conversationSurfaceRef.current,
                            epoch,
                            item.id,
                            detail.ok
                              ? { ok: true, messages: detail.value.messages }
                              : { ok: false, error: errorMessage(detail.error.code) },
                          )
                          conversationSurfaceRef.current = applied
                          if (!applied.mounted || applied.detailRequestEpoch !== epoch) return
                          if (applied.conversationId !== item.id) return
                          setMessages(applied.messages)
                          setMessagesStatus(applied.messagesStatus)
                          setMessagesError(applied.messagesError)
                          if (applied.messagesStatus === 'error' && applied.messagesError !== null) {
                            onError(applied.messagesError)
                          }
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
              {stopping && <span className="text-[13px] text-foreground/80">正在停止</span>}
              {(desktopLiveStopVisible(live) || desktopTeamStopVisible(teamLive)) && (
                <Button type="button" variant="outline" size="sm" onClick={() => void stopGeneration()}>
                  <Square className="h-4 w-4" />
                  停止
                </Button>
              )}
            </div>
          </div>
          {teamMode && (
            <div className="grid gap-3 border-b border-border p-3 text-[13px] md:grid-cols-2">
              <div>
                <div className="mb-1 font-medium">AI 员工</div>
                {projectDesktopTeamEmployees(teamLive).map((row) => (
                  <div key={row.roleId} className="flex justify-between gap-2">
                    <span>{row.label}</span>
                    <span>{row.statusText}</span>
                  </div>
                ))}
              </div>
              <div>
                <div className="mb-1 font-medium">节点时间线</div>
                {teamLive.planRevisionId !== null && (
                  <div>当前计划：{teamLive.planRevisionId}</div>
                )}
                {teamLive.waveId !== null && (
                  <div>
                    当前 wave：{teamLive.waveId}
                    {teamLive.declaredExecution !== null
                      ? `（声明 ${teamLive.declaredExecution}${
                          teamLive.effectiveExecution !== null &&
                          teamLive.effectiveExecution !== teamLive.declaredExecution
                            ? `，宿主降为 ${teamLive.effectiveExecution}`
                            : ''
                        }）`
                      : ''}
                  </div>
                )}
                {teamLive.planSummary !== null && teamLive.planSummary !== '' && (
                  <div>依赖：{teamLive.planSummary}</div>
                )}
                {projectDesktopTeamTimeline(teamLive).map((node) => (
                  <details key={node.nodeId} className="border border-border p-1">
                    <summary>
                      #{node.ordinal} {node.employeeRoleId} {node.statusText}{' '}
                      {node.durationMs !== null ? `${Math.round(node.durationMs / 100) / 10}s` : ''}{' '}
                      {node.totalTokens !== null ? `${node.totalTokens} tokens` : ''}
                    </summary>
                    <pre className="whitespace-pre-wrap break-words">{node.report ?? '尚无报告'}</pre>
                  </details>
                ))}
                {teamLive.collaborationLines.length > 0 && (
                  <div className="mt-2">
                    <div className="font-medium">协作请求</div>
                    {teamLive.collaborationLines.map((line) => (
                      <div key={line}>{line}</div>
                    ))}
                  </div>
                )}
              </div>
            </div>
          )}
          <div className="flex-1 space-y-4 overflow-y-auto p-4 text-[16px] leading-7">
            {messagesStatus === 'loading' && messages.length === 0 && (
              <div className="text-[13px] text-foreground/80">加载中</div>
            )}
            {messagesStatus === 'error' && messagesError !== null && (
              <div className="text-[13px]">{messagesError}</div>
            )}
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
            {teamProjection.visible && teamProjection.parentFinalAnswer && (
              <div className="space-y-1 border border-foreground/40 p-3">
                <div className="text-[13px] font-medium text-foreground">父 Agent 最终回答</div>
                <div className="whitespace-pre-wrap break-words">{teamProjection.parentFinalAnswer}</div>
              </div>
            )}
            {teamProjection.visible && teamProjection.parentLiveText !== '' && !teamProjection.parentFinalAnswer && (
              <div className="whitespace-pre-wrap break-words">{teamProjection.parentLiveText}</div>
            )}
            {stopping && liveProjection.visible && liveProjection.liveText === '' && (
              <div className="text-[13px] text-foreground/80">正在停止</div>
            )}
            <div ref={bottomRef} />
          </div>
          <form className="border-t border-border p-4" onSubmit={(event) => void sendMessage(event)}>
            <label className="mb-2 flex items-center gap-2 text-[13px]">
              <input
                type="checkbox"
                checked={teamMode}
                onChange={(event) => setTeamMode(event.target.checked)}
              />
              团队协作（Owner 任务级委托：父 Agent 判断编制，宿主校验后执行）
            </label>
            {teamMode && (
              <div className="mb-3 space-y-2 text-[13px]">
                <div>{projectDesktopTeamBudget(teamLive)}</div>
                <div className="flex items-center gap-2">
                  <Input
                    value={appendCalls}
                    onChange={(event) => setAppendCalls(event.target.value)}
                    className="h-8 w-24 rounded-none text-[13px]"
                    aria-label="追加调用预算"
                  />
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    disabled={teamLive.teamRunId === null}
                    onClick={() => {
                      if (workspaceId === null || teamLive.teamRunId === null) return
                      const nextCalls = Number.parseInt(appendCalls, 10)
                      if (!Number.isInteger(nextCalls)) return
                      const next = { ...teamBudget, maximumProviderCalls: nextCalls }
                      setTeamBudget(next)
                      void bridge.teamRuns.appendBudget({
                        workspaceId,
                        teamRunId: teamLive.teamRunId,
                        budget: next,
                      })
                    }}
                  >
                    追加预算
                  </Button>
                </div>
                <details>
                  <summary>允许父 Agent 使用的员工（默认全部允许，非每次任务编制）</summary>
                  <div className="mt-2 grid grid-cols-2 gap-1">
                    {TEAM_SPECIALISTS.map((role) => (
                      <label key={role} className="flex items-center gap-1">
                        <input
                          type="checkbox"
                          checked={allowedSpecialists.includes(role)}
                          onChange={(event) => {
                            setAllowedSpecialists((current) =>
                              event.target.checked
                                ? [...current, role]
                                : current.filter((item) => item !== role),
                            )
                          }}
                        />
                        {role}
                      </label>
                    ))}
                  </div>
                </details>
              </div>
            )}
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
                disabled={sendBlocked}
              >
                <RotateCcw className="h-4 w-4" />
                重试
              </Button>
              <Button type="submit" disabled={sendBlocked || draft.trim() === ''}>
                {sendBlocked ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <Send className="h-4 w-4" />
                )}
                发送
              </Button>
            </div>
          </form>
          {liveProjection.visible && liveProjection.liveMeta && (
            <button
              type="button"
              className="border-t border-border px-4 py-2 text-left text-[13px]"
              onClick={() => setDetailsOpen((value) => !value)}
            >
              调用详情 {detailsOpen ? '收起' : '展开'}
              {detailsOpen && (
                <div className="mt-2 space-y-1 text-foreground">
                  <div>请求模型：{liveProjection.liveMeta.requestedModel ?? '—'}</div>
                  <div>实际模型：{liveProjection.liveMeta.actualModel ?? '—'}</div>
                  <div>Provider：{liveProjection.liveMeta.providerName ?? '—'}</div>
                  <div>状态：{statusLabel(liveProjection.liveMeta.status ?? 'running')}</div>
                  <div>耗时：{liveProjection.liveMeta.durationMs ?? '—'} ms</div>
                  <div>Tokens：{liveProjection.liveMeta.totalTokens ?? '未提供'}</div>
                  <div>思考深度：{liveProjection.liveMeta.thinkingDepth ?? '—'}</div>
                  {liveProjection.liveMeta.errorRedacted && (
                    <div>错误：{liveProjection.liveMeta.errorRedacted}</div>
                  )}
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
