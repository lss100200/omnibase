'use client'

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  DesktopConversation,
  DesktopMessage,
  DesktopOwner,
  DesktopProvider,
  DesktopTeamRun,
  DesktopWorkspace,
  OmniBaseDesktopBridge,
  PersonalTeamBlackboard,
  beginDesktopLiveSend,
  completeDesktopLiveSend,
  createDesktopLiveStreamState,
  desktopInvocationCancelTarget,
  desktopInvocationIsStopping,
  desktopInvocationLiveProjection,
  desktopInvocationNeedsStreamAbort,
  desktopLiveSendBlocked,
  desktopLiveStopVisible,
  desktopTeamAppendBudgetTarget,
  markDesktopInvocationCancelDispatched,
  reduceDesktopInvocationEvent,
  requestDesktopLiveCancel,
  switchDesktopLiveScope,
  beginDesktopTeamRun,
  createDesktopTeamLiveState,
  desktopTeamLiveProjection,
  desktopTeamStopVisible,
  failDesktopTeamPreStart,
  pendingDurableTeamCancel,
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
import { desktopTeamEventBindsLiveRun } from '@/lib/desktop-team-lifecycle'
import {
  createP7LiveSlotState,
  invalidateP7LiveSlot,
  p7HistoryBoardForSelection,
  p7LiveSlotViewProjection,
  p7SelectionStaleInWorkspace,
  reduceP7LiveSlotEvent,
  selectP7HistoryRun,
  type P7LiveSlotState,
} from '@/lib/p7-live-slot'
import {
  p7BriefBoardSelection,
  p7ConversationEventLogLine,
  p7RunHistoryProjection,
  p7TeamEventLogLine,
} from '@/lib/p7-workbench-shell'
import { P7WorkbenchShell, type P7ProviderForm } from '@/components/workbench/p7/p7-shell'
import './p7-workbench.css'

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
  desktop_team_allow_list_empty: '已开启团队协作，但允许名单为空；未默认调用全部专员。',
  desktop_team_conversation_identity_mismatch: '团队启动绑定的会话与当前会话不一致。',
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

const TEAM_TERMINAL_EVENT_STATES: ReadonlySet<string> = new Set([
  'succeeded',
  'failed',
  'cancelled',
  'budget_exhausted',
  'cannot_complete',
  'unknown',
])

const MAX_EVENT_LOG_LINES = 300
const MAX_OUTPUT_LINES = 100

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
    () =>
      typeof navigator === 'undefined' ? true : navigator.language.toLowerCase().startsWith('zh'),
    [],
  )
  const [zoom, setZoom] = useState(100)
  const initialWorkspaceId = workspaces.find((item) => item.state === 'active')?.id ?? null
  const [workspaceId, setWorkspaceId] = useState<string | null>(initialWorkspaceId)
  const [conversations, setConversations] = useState<readonly DesktopConversation[]>([])
  const [conversationId, setConversationId] = useState<string | null>(null)
  const [messages, setMessages] = useState<readonly DesktopMessage[]>([])
  const [messagesStatus, setMessagesStatus] = useState<'empty' | 'loading' | 'ready' | 'error'>(
    'empty',
  )
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
  const durableTeamCancelRef = useRef<string | null>(null)
  const [teamMode, setTeamMode] = useState(false)
  const [allowedSpecialists, setAllowedSpecialists] = useState<readonly string[]>([
    ...TEAM_SPECIALISTS,
  ])
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

  // P7 Wave 1 wiring: run history, blackboards, live slot and logs.
  // The live/history slot transitions are a pure state machine
  // (lib/p7-live-slot.ts); this component only executes its effects.
  const [slotState, setSlotState] = useState<P7LiveSlotState>(createP7LiveSlotState)
  const slotStateRef = useRef(slotState)
  const [runHistory, setRunHistory] = useState<readonly DesktopTeamRun[]>([])
  const [runHistoryStatus, setRunHistoryStatus] = useState<'idle' | 'loading' | 'ready' | 'error'>(
    'idle',
  )
  // The history list is bound to the workspace it was loaded for; the
  // projection hides it the moment the view leaves that workspace, so the
  // first frame after a switch can never render or click old rows.
  const [runHistoryWorkspaceId, setRunHistoryWorkspaceId] = useState<string | null>(
    initialWorkspaceId,
  )
  // History blackboard: board of the run selected in the run panel. It never
  // drives OMNIA; browsing history must not move the live widget.
  const [historyBlackboard, setHistoryBlackboard] = useState<PersonalTeamBlackboard | null>(null)
  const [historyBlackboardStatus, setHistoryBlackboardStatus] = useState<
    'idle' | 'loading' | 'ready' | 'error'
  >('idle')
  // Live blackboard: board of the currently executing run only. OMNIA reads
  // this slot, so its pending count is always about the current run.
  const [liveBlackboard, setLiveBlackboard] = useState<PersonalTeamBlackboard | null>(null)
  const [liveBlackboardStatus, setLiveBlackboardStatus] = useState<
    'idle' | 'loading' | 'ready' | 'error'
  >('idle')
  // Scoped task text: the last team task per workspace:conversation, so a
  // completed team run never leaks into another conversation's feed.
  const [taskTextByScope, setTaskTextByScope] = useState<Readonly<Record<string, string>>>({})
  const [eventLog, setEventLog] = useState<readonly string[]>([])
  const [outputLines, setOutputLines] = useState<readonly string[]>([])
  const [bridgeSubscribed, setBridgeSubscribed] = useState(false)
  const liveBlackboardEpochRef = useRef(0)
  const historyBlackboardEpochRef = useRef(0)
  const runHistoryEpochRef = useRef(0)

  const pushOutput = useCallback((line: string) => {
    setOutputLines((current) => [...current, line].slice(-MAX_OUTPUT_LINES))
  }, [])
  const appendEvent = useCallback((line: string) => {
    setEventLog((current) => [...current, line].slice(-MAX_EVENT_LOG_LINES))
  }, [])

  const applySlotState = useCallback((next: P7LiveSlotState) => {
    slotStateRef.current = next
    setSlotState(next)
  }, [])

  /**
   * Invalidates the live part of the slot on a new run attempt: bumps its
   * epoch (so in-flight responses are dropped) and clears the board. A
   * failed startup must never restore a previous run's pending projection;
   * the history selection survives.
   */
  const invalidateLiveBoard = useCallback(() => {
    liveBlackboardEpochRef.current += 1
    setLiveBlackboard(null)
    setLiveBlackboardStatus('idle')
  }, [])

  const applyViewScope = useCallback(
    (nextWorkspaceId: string | null, nextConversationId: string | null) => {
      const next = advanceDesktopSurfaceScope(
        surfaceScopeRef.current,
        nextWorkspaceId,
        nextConversationId,
      )
      surfaceScopeRef.current = next
      setWorkspaceId(next.workspaceId)
      setConversationId(next.conversationId)
      const nextLive = switchDesktopLiveScope(
        liveRef.current,
        next.workspaceId,
        next.conversationId,
      )
      liveRef.current = nextLive
      setLive(nextLive)
      const nextTeam = switchDesktopTeamScope(
        teamLiveRef.current,
        next.workspaceId,
        next.conversationId,
      )
      teamLiveRef.current = nextTeam
      setTeamLive(nextTeam)
      return next
    },
    [],
  )
  const [submitting, setSubmitting] = useState(false)
  const [providerForm, setProviderForm] = useState<P7ProviderForm>({
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

  const liveProjection = desktopInvocationLiveProjection(live, workspaceId, conversationId)
  const teamProjection = desktopTeamLiveProjection(teamLive, workspaceId, conversationId)
  const teamAppendBudgetTarget = desktopTeamAppendBudgetTarget(teamLive)
  const sendBlocked = desktopLiveSendBlocked(live) || desktopTeamStopVisible(teamLive)
  const stopVisible = desktopLiveStopVisible(live) || desktopTeamStopVisible(teamLive)
  const stopping = desktopInvocationIsStopping(live) || teamLive.phase === 'cancelling'

  const loadRunHistory = useCallback(
    async (nextWorkspaceId: string) => {
      // Never leave the previous workspace's runs visible while loading.
      setRunHistory([])
      setRunHistoryStatus('loading')
      setRunHistoryWorkspaceId(nextWorkspaceId)
      const epoch = ++runHistoryEpochRef.current
      const result = await bridge.teamRuns.list({ workspaceId: nextWorkspaceId })
      if (!mountedRef.current) return
      if (surfaceScopeRef.current.workspaceId !== nextWorkspaceId) return
      if (epoch !== runHistoryEpochRef.current) return
      if (!result.ok) {
        setRunHistoryStatus('error')
        return
      }
      setRunHistory(result.value.items)
      setRunHistoryStatus('ready')
      const currentSlot = slotStateRef.current
      if (
        p7SelectionStaleInWorkspace({
          historyWorkspaceId: currentSlot.historyOriginWorkspaceId,
          loadedWorkspaceId: nextWorkspaceId,
          historyRunId: currentSlot.historyRunId,
          runs: result.value.items,
        })
      ) {
        applySlotState({
          ...currentSlot,
          historyRunId: null,
          historyOriginKey: null,
          historyOriginWorkspaceId: null,
          historyIsManual: false,
        })
      }
    },
    [applySlotState, bridge],
  )

  const loadHistoryBlackboard = useCallback(
    async (nextWorkspaceId: string, teamRunId: string) => {
      const epoch = ++historyBlackboardEpochRef.current
      // Every history reload drops the previous payload first: a terminal
      // refresh (or a failed reload) must never render an old snapshot of
      // the same run together with loading/error states.
      setHistoryBlackboard(null)
      setHistoryBlackboardStatus('loading')
      const result = await bridge.teamRuns.getBlackboard({
        workspaceId: nextWorkspaceId,
        teamRunId,
      })
      if (!mountedRef.current) return
      if (epoch !== historyBlackboardEpochRef.current) return
      if (slotStateRef.current.historyRunId !== teamRunId) return
      if (!result.ok) {
        setHistoryBlackboardStatus('error')
        return
      }
      setHistoryBlackboard(result.value.blackboard)
      setHistoryBlackboardStatus('ready')
      pushOutput(`已读取运行 ${teamRunId} 的黑板`)
    },
    [bridge, pushOutput],
  )

  const loadLiveBlackboard = useCallback(
    async (nextWorkspaceId: string, teamRunId: string) => {
      const epoch = ++liveBlackboardEpochRef.current
      setLiveBlackboardStatus('loading')
      const result = await bridge.teamRuns.getBlackboard({
        workspaceId: nextWorkspaceId,
        teamRunId,
      })
      if (!mountedRef.current) return
      if (epoch !== liveBlackboardEpochRef.current) return
      // Identity gate: the live run may have advanced while the request was
      // in flight; only a board matching the current live run may land.
      if (slotStateRef.current.liveRunId !== teamRunId) return
      if (!result.ok) {
        setLiveBlackboardStatus('error')
        return
      }
      setLiveBlackboard(result.value.blackboard)
      setLiveBlackboardStatus('ready')
    },
    [bridge],
  )

  const selectRun = useCallback(
    (teamRunId: string) => {
      if (workspaceId === null) return
      applySlotState(
        selectP7HistoryRun(slotStateRef.current, teamRunId, workspaceId, conversationId),
      )
      setHistoryBlackboard(null)
      void loadHistoryBlackboard(workspaceId, teamRunId)
    },
    [applySlotState, conversationId, loadHistoryBlackboard, workspaceId],
  )

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
          ? (active.find((item) => item.id === conversationSurfaceRef.current.conversationId) ??
            active[0])
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
      conversationSurfaceRef.current = unmountDesktopConversationSurface(
        conversationSurfaceRef.current,
      )
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
    if (workspaceId !== null) void loadRunHistory(workspaceId)
    // The slot and blackboards are NOT reset on a workspace switch: an
    // origin-scoped background run in another workspace must keep its
    // selection and prefetched final board recoverable. Cross-workspace
    // display is already blocked by the projection identity gates.
  }, [loadRunHistory, workspaceId])

  useEffect(() => {
    const unsubscribe = bridge.conversations.subscribe((event) => {
      appendEvent(`[conversation] ${p7ConversationEventLogLine(event)}`)
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
    setBridgeSubscribed(true)
    return () => {
      setBridgeSubscribed(false)
      unsubscribe()
    }
  }, [appendEvent, bridge, onError])

  useEffect(() => {
    const unsubscribe = bridge.teamRuns.subscribe((event) => {
      appendEvent(`[team] ${p7TeamEventLogLine(event)}`)
      const next = reduceDesktopTeamEvent(teamLiveRef.current, event)
      teamLiveRef.current = next
      setTeamLive(next)
      // The slot transition is decided by the pure state machine
      // (lib/p7-live-slot.ts); this handler only executes its effects.
      // Identity comes from the predicate (origin + roster + bound run id),
      // never from the reducer's acceptance (phase/eligibility based) and
      // never from the visible phase (hidden as `idle` while parked).
      const currentSlot = slotStateRef.current
      const transition = reduceP7LiveSlotEvent(currentSlot, {
        eventRunId: event.teamRunId ?? null,
        eventWorkspaceId: event.workspaceId,
        eventConversationId: event.conversationId ?? null,
        isTerminal: event.state !== undefined && TEAM_TERMINAL_EVENT_STATES.has(event.state),
        bindsLiveRun: desktopTeamEventBindsLiveRun(teamLiveRef.current, event),
        viewWorkspaceId: surfaceScopeRef.current.workspaceId,
        viewConversationId: surfaceScopeRef.current.conversationId,
        boardChanged:
          event.type === 'blackboard' ||
          event.type === 'plan_transition' ||
          event.type === 'proposal',
      })
      applySlotState(transition.state)
      // The selection moved to another run (auto-follow or terminal landing):
      // the old run's payload must never render under the new selection, so
      // drop it the moment the selection identity changes.
      if (transition.state.historyRunId !== currentSlot.historyRunId) {
        setHistoryBlackboard(null)
        setHistoryBlackboardStatus('idle')
      }
      if (transition.effects.loadLiveBoard && transition.state.liveRunId !== null) {
        void loadLiveBlackboard(event.workspaceId, transition.state.liveRunId)
      }
      if (transition.effects.loadHistoryBoard && transition.state.historyRunId !== null) {
        void loadHistoryBlackboard(event.workspaceId, transition.state.historyRunId)
      }
      if (transition.effects.refreshRunHistory) {
        void loadRunHistory(event.workspaceId)
      }
    })
    setBridgeSubscribed(true)
    return () => {
      setBridgeSubscribed(false)
      unsubscribe()
    }
  }, [
    appendEvent,
    applySlotState,
    bridge,
    loadHistoryBlackboard,
    loadLiveBlackboard,
    loadRunHistory,
  ])

  useEffect(() => {
    const pending = pendingDurableTeamCancel(teamLive, durableTeamCancelRef.current)
    if (pending === null) return
    durableTeamCancelRef.current = pending
    void bridge.teamRuns.cancel({
      workspaceId: teamLive.originWorkspaceId ?? workspaceId ?? '',
      teamRunId: pending,
    })
  }, [teamLive, bridge, workspaceId])

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
    pushOutput(`已创建会话：${created.value.conversation.title}`)
    return created.value.conversation.id
  }

  const ensureConversation = async (): Promise<string | null> => {
    if (workspaceId === null) return null
    if (conversationId !== null) return conversationId
    return createConversation()
  }

  const createWorkspace = async (name: string) => {
    const trimmed = name.trim()
    if (trimmed === '') return
    const result = await bridge.workspaces.create({ name: trimmed })
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
    // The new workspace takes over synchronously: run history from any
    // previous workspace must not survive the first frame.
    runHistoryEpochRef.current += 1
    setRunHistory([])
    setRunHistoryStatus('loading')
    setRunHistoryWorkspaceId(result.value.workspace.id)
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
    pushOutput(`已创建工作空间：${result.value.workspace.name}`)
  }

  const archiveConversation = async (targetId: string) => {
    const current = conversations.find((item) => item.id === targetId)
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
    pushOutput(`已归档会话：${current.title}`)
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

  const sendMessage = async () => {
    const content = draft.trim()
    if (
      workspaceId === null ||
      content === '' ||
      desktopLiveSendBlocked(live) ||
      desktopLiveSendBlocked(liveRef.current) ||
      desktopTeamStopVisible(teamLiveRef.current)
    )
      return
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
      // A new run attempt invalidates the live slot immediately: no old
      // board may project onto the new run's preparing phase, and a failed
      // startup must not restore the previous run's pending view.
      applySlotState(invalidateP7LiveSlot(slotStateRef.current))
      invalidateLiveBoard()
      setTaskTextByScope((current) => ({
        ...current,
        [`${workspaceId}:${target}`]: routed.message,
      }))
      const allowed =
        allowedSpecialists.length === TEAM_SPECIALISTS.length ? undefined : allowedSpecialists
      pushOutput(`已启动团队运行：${routed.message}`)
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
        const failed = failDesktopTeamPreStart(teamLiveRef.current)
        teamLiveRef.current = failed
        setTeamLive(failed)
        pushOutput(`团队运行启动失败：${errorMessage(result.error.code)}`)
        onError(errorMessage(result.error.code))
        return
      }
      if (desktopTeamStopVisible(teamLiveRef.current)) {
        const failed = failDesktopTeamPreStart(teamLiveRef.current)
        teamLiveRef.current = failed
        setTeamLive(failed)
      }
      // Bring the just-started run into history immediately; terminal events
      // refresh it again when the run finishes.
      void loadRunHistory(workspaceId)
      if (result.value.proof.state === 'budget_exhausted') {
        pushOutput('团队预算耗尽；未伪造完成。')
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
    if (
      completed.phase === 'idle' &&
      (completed.terminalStatus === 'cancelled' || (result.ok && result.value.type === 'cancelled'))
    ) {
      onError('生成已停止')
    }
    if (!result.ok) {
      if (completed.terminalStatus === 'cancelled') {
        onError('生成已停止')
      } else if (
        applyDesktopSurfaceError(conversationSurfaceRef.current, completion.epoch, 'detail')
      ) {
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
    pushOutput('已请求停止')
    if (teamStop) {
      const cancelledTeam = requestDesktopTeamCancel(teamCurrent)
      teamLiveRef.current = cancelledTeam
      setTeamLive(cancelledTeam)
      if (mountedRef.current) onError('正在停止')
      await bridge.conversations.abortInFlightSend()
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
    if (
      workspaceId === null ||
      conversationId === null ||
      desktopLiveSendBlocked(live) ||
      desktopLiveSendBlocked(liveRef.current)
    )
      return
    const failed = [...messages]
      .reverse()
      .find((item) => item.role === 'assistant' && item.status !== 'completed')
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
    if (
      completed.phase === 'idle' &&
      (completed.terminalStatus === 'cancelled' || (result.ok && result.value.type === 'cancelled'))
    ) {
      onError('生成已停止')
    }
    if (!result.ok) {
      if (completed.terminalStatus === 'cancelled') {
        onError('生成已停止')
      } else if (
        applyDesktopSurfaceError(conversationSurfaceRef.current, completion.epoch, 'detail')
      ) {
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

  const saveProvider = async () => {
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
      pushOutput(`Provider 已保存：${providerForm.displayName}`)
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
    const line = result.value.ok
      ? `测试通过 · ${familyLabel(result.value.family)} · ${
          result.value.identityProven
            ? (result.value.actualModel ?? result.value.requestedModel)
            : '模型身份未证明'
        }`
      : (result.value.errorRedacted ?? '测试失败')
    setTestResult(line)
    pushOutput(line)
  }

  const appendBudget = (nextCalls: number) => {
    if (teamAppendBudgetTarget === null) return
    const next = { ...teamBudget, maximumProviderCalls: nextCalls }
    setTeamBudget(next)
    void bridge.teamRuns.appendBudget({
      workspaceId: teamAppendBudgetTarget.workspaceId,
      teamRunId: teamAppendBudgetTarget.teamRunId,
      budget: next,
    })
    pushOutput(`已追加预算：上限 ${nextCalls} 次调用`)
  }

  const selectWorkspace = (nextWorkspaceId: string) => {
    // Invalidate the run history synchronously with the scope switch: the
    // first frame after the switch must not render or allow clicks on the
    // previous workspace's runs.
    runHistoryEpochRef.current += 1
    setRunHistory([])
    setRunHistoryStatus('loading')
    setRunHistoryWorkspaceId(nextWorkspaceId)
    applyViewScope(nextWorkspaceId, null)
    conversationSurfaceRef.current = selectDesktopConversation(
      conversationSurfaceRef.current,
      nextWorkspaceId,
      null,
    )
    setConversations([])
    setMessages([])
    setMessagesStatus('empty')
    setMessagesError(null)
  }

  const selectConversation = (nextConversationId: string) => {
    if (workspaceId === null) return
    applyViewScope(workspaceId, nextConversationId)
    const selected = selectDesktopConversation(
      conversationSurfaceRef.current,
      workspaceId,
      nextConversationId,
    )
    conversationSurfaceRef.current = selected
    setMessages([])
    setMessagesStatus('loading')
    setMessagesError(null)
    const epoch = selected.detailRequestEpoch
    void bridge.conversations
      .get({ workspaceId, conversationId: nextConversationId })
      .then((detail) => {
        const applied = applyDesktopConversationDetail(
          conversationSurfaceRef.current,
          epoch,
          nextConversationId,
          detail.ok
            ? { ok: true, messages: detail.value.messages }
            : { ok: false, error: errorMessage(detail.error.code) },
        )
        conversationSurfaceRef.current = applied
        if (!applied.mounted || applied.detailRequestEpoch !== epoch) return
        if (applied.conversationId !== nextConversationId) return
        setMessages(applied.messages)
        setMessagesStatus(applied.messagesStatus)
        setMessagesError(applied.messagesError)
        if (applied.messagesStatus === 'error' && applied.messagesError !== null) {
          onError(applied.messagesError)
        }
      })
  }

  // The live slot only applies while the user views the live run's origin
  // conversation; elsewhere the brief falls back to the history selection,
  // which itself is conversation-scoped when auto-followed.
  const slotView = p7LiveSlotViewProjection(slotState, workspaceId, conversationId)
  const brief = p7BriefBoardSelection({
    liveCurrent: slotView.liveCurrent,
    liveBoard: liveBlackboard,
    liveStatus: liveBlackboardStatus,
    historyBoard: p7HistoryBoardForSelection(slotView.selectionRunId, historyBlackboard),
    historyStatus: slotView.selectionVisible ? historyBlackboardStatus : 'idle',
  })
  const runHistoryView = p7RunHistoryProjection({
    historyWorkspaceId: runHistoryWorkspaceId,
    viewWorkspaceId: workspaceId,
    status: runHistoryStatus,
    rows: runHistory,
  })

  return (
    <P7WorkbenchShell
      version={version}
      owner={owner}
      chinese={chinese}
      zoom={zoom}
      onZoomChange={setZoom}
      workspaces={workspaces}
      workspaceId={workspaceId}
      conversations={conversations}
      conversationId={conversationId}
      onSelectWorkspace={(next) => selectWorkspace(next)}
      onCreateWorkspace={(name) => void createWorkspace(name)}
      onSelectConversation={(next) => selectConversation(next)}
      onCreateConversation={() => void createConversation()}
      onArchiveConversation={(conversationId) => void archiveConversation(conversationId)}
      workspaceNameInput={workspaceName}
      onWorkspaceNameInputChange={setWorkspaceName}
      messages={messages}
      messagesStatus={messagesStatus}
      messagesError={messagesError}
      agentName={agentName}
      teamProjection={teamProjection}
      liveProjection={liveProjection}
      stopping={stopping}
      teamLive={teamLive}
      taskText={taskTextByScope[`${workspaceId}:${conversationId}`] ?? null}
      teamMode={teamMode}
      onTeamModeChange={setTeamMode}
      allowedSpecialists={allowedSpecialists}
      onAllowedSpecialistsChange={setAllowedSpecialists}
      teamBudget={teamBudget}
      appendCalls={appendCalls}
      onAppendCallsChange={setAppendCalls}
      teamAppendBudgetTarget={teamAppendBudgetTarget}
      onAppendBudget={(calls) => appendBudget(calls)}
      runHistory={runHistoryView.rows}
      runHistoryStatus={runHistoryView.status}
      selectedRunId={slotView.selectionRunId}
      onSelectRun={(teamRunId) => selectRun(teamRunId)}
      blackboard={brief.board}
      blackboardStatus={brief.status}
      liveRunId={slotState.liveRunId}
      liveBlackboard={liveBlackboard}
      liveCurrent={slotView.liveCurrent}
      draft={draft}
      onDraftChange={setDraft}
      onSend={() => void sendMessage()}
      onRetry={() => void retryLast()}
      onStop={() => void stopGeneration()}
      sendBlocked={sendBlocked}
      stopVisible={stopVisible}
      providerForm={providerForm}
      onProviderFormChange={(patch) => setProviderForm((current) => ({ ...current, ...patch }))}
      onSaveProvider={() => void saveProvider()}
      submitting={submitting}
      testResult={testResult}
      providers={providers}
      onTestProvider={(providerId) => void testSelected(providerId)}
      eventLog={eventLog}
      outputLines={outputLines}
      bridgeSubscribed={bridgeSubscribed}
      live={{
        conversationId: live.conversationId,
        invocationId: live.invocationId,
        phase: live.phase,
      }}
    />
  )
}
