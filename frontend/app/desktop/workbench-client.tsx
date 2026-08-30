'use client'

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  DesktopConversation,
  DesktopApplicationPreference,
  DesktopMessage,
  DesktopOwner,
  DesktopProvider,
  DesktopTeamRun,
  DesktopWorkspace,
  DesktopWorkbenchDensity,
  DesktopWorkspaceCompositionProfileValue,
  DesktopWorkspaceCompositionProposal,
  DesktopWorkspaceCompositionSnapshot,
  DesktopWorkspaceComponentCatalogItem,
  DesktopWorkspaceComponentEffect,
  DesktopWorkspaceComponentInstallation,
  DesktopWorkspaceComponentLifecycleAction,
  DesktopWorkspaceComponentProposal,
  DesktopWorkspaceComponentSnapshot,
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
  type DesktopWorkspaceFileAuthorization,
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
import {
  p7CloneCompositionProfile,
  p7CompositionAssistantPrompt,
  p7CompositionProjection,
  p7FindNewAssistantCompositionMessage,
  p7WorkspaceSelectionChangesScope,
  type P7CompositionLoadStatus,
} from '@/lib/p7-workspace-composition'
import {
  createP7WorkspaceComponentSurfaceState,
  p7AssistantDeclarativePackagePrompt,
  p7ReconcileWorkspaceComponentSurfaces,
  p7EnterWorkspaceComponentSafeMode,
  p7FindNewCompletedComponentAssistantMessage,
  p7ParseAssistantDeclarativePackage,
  p7SetWorkspaceComponentSurface,
  p7WorkspaceComponentCommittedUiBindings,
  p7WorkspaceComponentAssistantPrompt,
  p7WorkspaceComponentHostSlotId,
  p7WorkspaceComponentResultEventLogLine,
  p7WorkspaceComponentSurfaceProjection,
  p7WorkspaceComponentSurfaceRequests,
  p7WorkspaceComponentsProjection,
  type P7AssistantDeclarativePackageReview,
  type P7WorkspaceComponentSurfaceState,
  type P7WorkspaceComponentsLoadStatus,
} from '@/lib/p7-workspace-components'
import {
  beginP7WorkspaceDirectoryList,
  beginP7WorkspaceFileAuthorization,
  beginP7WorkspaceFileRead,
  createP7WorkspaceFilesState,
  failP7WorkspaceDirectoryList,
  failP7WorkspaceFileAuthorization,
  failP7WorkspaceFileRead,
  p7WorkspaceFileDirectory,
  p7WorkspaceFileErrorMessage,
  p7WorkspaceFilesAuthorized,
  parseP7WorkspaceFileAuthorization,
  releaseP7WorkspaceFilesAuthorization,
  setP7WorkspaceDirectoryExpanded,
  settleP7WorkspaceDirectoryList,
  settleP7WorkspaceFileAuthorization,
  settleP7WorkspaceFileRead,
  switchP7WorkspaceFilesWorkspace,
  type P7WorkspaceFilesState,
} from '@/lib/p7-workspace-files'
import { P7WorkbenchShell, type P7ProviderForm } from '@/components/workbench/p7/p7-shell'
import type {
  P7WorkspaceComponentInvokeRequest,
  P7WorkspaceComponentProposalDraft,
} from '@/components/workbench/p7/settings/p7-workspace-component-views'
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
  desktop_workbench_preference_version_conflict: '应用设置已经发生变化，已重新读取最新值。',
  desktop_composition_version_conflict: 'Workspace Profile 已更新，请基于最新版本重新提案。',
  desktop_composition_no_change: '提案与当前 Workspace Profile 相同。',
  desktop_composition_capability_unavailable: '该组件没有可信数据源，不能在 Profile 中启用。',
  desktop_composition_assistant_payload_invalid: 'Agent 没有返回可验证的完整 Profile 提案。',
  desktop_composition_assistant_reference_invalid: 'Agent 提案的消息身份无法验证。',
  desktop_composition_proposal_decided: '该提案已经被处理。',
}

function errorMessage(code: string): string {
  return ERROR_MESSAGES[code] ?? '操作未完成；本机服务已安全拒绝该请求。'
}

function p7ComponentIdempotencyKey(action: string): string {
  const random = globalThis.crypto?.randomUUID?.().replaceAll('-', '')
  if (random === undefined) throw new Error('workspace_component_random_identity_unavailable')
  return `p73_${action}_${random}`
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
  const [workspaceFiles, setWorkspaceFiles] = useState<P7WorkspaceFilesState>(() =>
    createP7WorkspaceFilesState(initialWorkspaceId),
  )
  const workspaceFilesRef = useRef(workspaceFiles)
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

  const applyWorkspaceFilesState = useCallback((next: P7WorkspaceFilesState) => {
    workspaceFilesRef.current = next
    setWorkspaceFiles(next)
  }, [])

  const releaseNativeWorkspaceFiles = useCallback(
    async (authorization: DesktopWorkspaceFileAuthorization) => {
      const result = await bridge.workspaceFiles.release({
        workspaceId: authorization.workspaceId,
        authorizationGeneration: authorization.authorizationGeneration,
      })
      if (!mountedRef.current || result.ok) return
      if (
        result.error.code === 'desktop_workspace_files_not_authorized' ||
        result.error.code === 'desktop_workspace_files_generation_conflict'
      ) {
        return
      }
      onError(p7WorkspaceFileErrorMessage(result.error.code))
    },
    [bridge, onError],
  )

  const moveWorkspaceFilesScope = useCallback(
    (nextWorkspaceId: string | null) => {
      const current = workspaceFilesRef.current
      if (current.workspaceId === nextWorkspaceId) return
      const released = releaseP7WorkspaceFilesAuthorization(current)
      const next = switchP7WorkspaceFilesWorkspace(released.state, nextWorkspaceId)
      applyWorkspaceFilesState(next)
      if (released.authorization !== null) {
        void releaseNativeWorkspaceFiles(released.authorization)
      }
    },
    [applyWorkspaceFilesState, releaseNativeWorkspaceFiles],
  )

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
      moveWorkspaceFilesScope(nextWorkspaceId)
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
    [moveWorkspaceFilesScope],
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
  const [applicationPreference, setApplicationPreference] =
    useState<DesktopApplicationPreference | null>(null)
  const applicationPreferenceRef = useRef<DesktopApplicationPreference | null>(null)
  const [applicationPreferenceStatus, setApplicationPreferenceStatus] =
    useState<P7CompositionLoadStatus>('idle')
  const applicationPreferenceEpochRef = useRef(0)
  const applicationPreferenceBusyRef = useRef(false)
  const [compositionWorkspaceId, setCompositionWorkspaceId] = useState<string | null>(
    initialWorkspaceId,
  )
  const [compositionStatus, setCompositionStatus] = useState<P7CompositionLoadStatus>(
    initialWorkspaceId === null ? 'idle' : 'loading',
  )
  const [compositionSnapshot, setCompositionSnapshot] =
    useState<DesktopWorkspaceCompositionSnapshot | null>(null)
  const compositionSnapshotRef = useRef<DesktopWorkspaceCompositionSnapshot | null>(null)
  const [compositionDraft, setCompositionDraft] =
    useState<DesktopWorkspaceCompositionProfileValue | null>(null)
  const [compositionIntent, setCompositionIntent] = useState('')
  const [compositionBusy, setCompositionBusy] = useState(false)
  const compositionBusyRef = useRef(false)
  const [compositionNotice, setCompositionNotice] = useState<string | null>(null)
  const compositionLoadEpochRef = useRef(0)
  const compositionOperationEpochRef = useRef(0)
  const [componentsWorkspaceId, setComponentsWorkspaceId] = useState<string | null>(
    initialWorkspaceId,
  )
  const [componentsStatus, setComponentsStatus] = useState<P7WorkspaceComponentsLoadStatus>(
    initialWorkspaceId === null ? 'idle' : 'loading',
  )
  const [componentsSnapshot, setComponentsSnapshot] =
    useState<DesktopWorkspaceComponentSnapshot | null>(null)
  const componentsSnapshotRef = useRef<DesktopWorkspaceComponentSnapshot | null>(null)
  const [componentsBusy, setComponentsBusy] = useState(false)
  const componentsBusyRef = useRef(false)
  const [componentsNotice, setComponentsNotice] = useState<string | null>(null)
  const [componentIntent, setComponentIntent] = useState('')
  const [assistantPackageReview, setAssistantPackageReview] =
    useState<P7AssistantDeclarativePackageReview | null>(null)
  const componentsLoadEpochRef = useRef(0)
  const componentsOperationEpochRef = useRef(0)
  const [componentSurfaceState, setComponentSurfaceState] =
    useState<P7WorkspaceComponentSurfaceState>(() =>
      createP7WorkspaceComponentSurfaceState(initialWorkspaceId),
    )
  const componentSurfaceStateRef = useRef(componentSurfaceState)
  const componentSurfaceInFlightRef = useRef(new Set<string>())

  useEffect(() => {
    componentSurfaceStateRef.current = componentSurfaceState
  }, [componentSurfaceState])

  const liveProjection = desktopInvocationLiveProjection(live, workspaceId, conversationId)
  const teamProjection = desktopTeamLiveProjection(teamLive, workspaceId, conversationId)
  const teamAppendBudgetTarget = desktopTeamAppendBudgetTarget(teamLive)
  const sendBlocked = desktopLiveSendBlocked(live) || desktopTeamStopVisible(teamLive)
  const stopVisible = desktopLiveStopVisible(live) || desktopTeamStopVisible(teamLive)
  const stopping = desktopInvocationIsStopping(live) || teamLive.phase === 'cancelling'

  const applyApplicationPreference = useCallback((value: DesktopApplicationPreference | null) => {
    applicationPreferenceRef.current = value
    setApplicationPreference(value)
  }, [])

  const loadApplicationPreference = useCallback(async () => {
    const epoch = ++applicationPreferenceEpochRef.current
    setApplicationPreferenceStatus('loading')
    const result = await bridge.workbenchSettings.get()
    if (!mountedRef.current || epoch !== applicationPreferenceEpochRef.current) return false
    if (!result.ok) {
      applyApplicationPreference(null)
      setApplicationPreferenceStatus('error')
      onError(errorMessage(result.error.code))
      return false
    }
    applyApplicationPreference(result.value.preference)
    setApplicationPreferenceStatus('ready')
    return true
  }, [applyApplicationPreference, bridge, onError])

  const updateApplicationPreference = useCallback(
    async (density: DesktopWorkbenchDensity, reduceMotion: boolean) => {
      const current = applicationPreferenceRef.current
      if (current === null || applicationPreferenceBusyRef.current) return
      if (current.density === density && current.reduceMotion === reduceMotion) return
      applicationPreferenceBusyRef.current = true
      const epoch = ++applicationPreferenceEpochRef.current
      setApplicationPreferenceStatus('loading')
      const result = await bridge.workbenchSettings.update({
        density,
        reduceMotion,
        expectedRowVersion: current.rowVersion,
      })
      if (!mountedRef.current || epoch !== applicationPreferenceEpochRef.current) {
        applicationPreferenceBusyRef.current = false
        return
      }
      applicationPreferenceBusyRef.current = false
      if (!result.ok) {
        setApplicationPreferenceStatus('error')
        onError(errorMessage(result.error.code))
        void loadApplicationPreference()
        return
      }
      applyApplicationPreference(result.value.preference)
      setApplicationPreferenceStatus('ready')
      pushOutput(`应用设置已更新：${density === 'compact' ? '紧凑' : '舒适'}`)
    },
    [applyApplicationPreference, bridge, loadApplicationPreference, onError, pushOutput],
  )

  const applyCompositionSnapshot = useCallback(
    (value: DesktopWorkspaceCompositionSnapshot | null) => {
      compositionSnapshotRef.current = value
      setCompositionSnapshot(value)
      setCompositionDraft(value === null ? null : p7CloneCompositionProfile(value.profile.value))
    },
    [],
  )

  const invalidateCompositionForWorkspace = useCallback(
    (nextWorkspaceId: string | null) => {
      compositionLoadEpochRef.current += 1
      compositionOperationEpochRef.current += 1
      compositionBusyRef.current = false
      setCompositionBusy(false)
      setCompositionWorkspaceId(nextWorkspaceId)
      setCompositionStatus(nextWorkspaceId === null ? 'idle' : 'loading')
      applyCompositionSnapshot(null)
      setCompositionIntent('')
      setCompositionNotice(null)
    },
    [applyCompositionSnapshot],
  )

  const loadComposition = useCallback(
    async (nextWorkspaceId: string) => {
      const epoch = ++compositionLoadEpochRef.current
      setCompositionWorkspaceId(nextWorkspaceId)
      setCompositionStatus('loading')
      applyCompositionSnapshot(null)
      const result = await bridge.workspaceComposition.get({ workspaceId: nextWorkspaceId })
      if (!mountedRef.current || epoch !== compositionLoadEpochRef.current) return false
      if (surfaceScopeRef.current.workspaceId !== nextWorkspaceId) return false
      if (!result.ok) {
        setCompositionStatus('error')
        setCompositionNotice(errorMessage(result.error.code))
        return false
      }
      if (result.value.profile.workspaceId !== nextWorkspaceId) {
        setCompositionStatus('error')
        setCompositionNotice('Workspace Profile 身份不一致，已拒绝投影。')
        return false
      }
      applyCompositionSnapshot(result.value)
      setCompositionStatus('ready')
      return true
    },
    [applyCompositionSnapshot, bridge],
  )

  const beginCompositionOperation = useCallback(() => {
    if (compositionBusyRef.current) return null
    const nextWorkspaceId = surfaceScopeRef.current.workspaceId
    const snapshot = compositionSnapshotRef.current
    if (
      nextWorkspaceId === null ||
      snapshot === null ||
      snapshot.profile.workspaceId !== nextWorkspaceId
    ) {
      return null
    }
    compositionBusyRef.current = true
    setCompositionBusy(true)
    setCompositionNotice(null)
    return Object.freeze({
      epoch: ++compositionOperationEpochRef.current,
      workspaceId: nextWorkspaceId,
      snapshot,
    })
  }, [])

  const compositionOperationIsCurrent = useCallback(
    (epoch: number, nextWorkspaceId: string) =>
      mountedRef.current &&
      compositionOperationEpochRef.current === epoch &&
      surfaceScopeRef.current.workspaceId === nextWorkspaceId,
    [],
  )

  const finishCompositionOperation = useCallback((epoch: number) => {
    if (compositionOperationEpochRef.current !== epoch) return
    compositionBusyRef.current = false
    setCompositionBusy(false)
  }, [])

  const applyComponentsSnapshot = useCallback((value: DesktopWorkspaceComponentSnapshot | null) => {
    componentsSnapshotRef.current = value
    setComponentsSnapshot(value)
  }, [])

  const invalidateComponentsForWorkspace = useCallback(
    (nextWorkspaceId: string | null) => {
      componentsLoadEpochRef.current += 1
      componentsOperationEpochRef.current += 1
      componentsBusyRef.current = false
      setComponentsBusy(false)
      setComponentsWorkspaceId(nextWorkspaceId)
      setComponentsStatus(nextWorkspaceId === null ? 'idle' : 'loading')
      applyComponentsSnapshot(null)
      componentSurfaceInFlightRef.current.clear()
      const nextSurfaceState = createP7WorkspaceComponentSurfaceState(nextWorkspaceId)
      componentSurfaceStateRef.current = nextSurfaceState
      setComponentSurfaceState(nextSurfaceState)
      setComponentIntent('')
      setAssistantPackageReview(null)
      setComponentsNotice(null)
    },
    [applyComponentsSnapshot],
  )

  const loadComponents = useCallback(
    async (nextWorkspaceId: string) => {
      const epoch = ++componentsLoadEpochRef.current
      setComponentsWorkspaceId(nextWorkspaceId)
      setComponentsStatus('loading')
      applyComponentsSnapshot(null)
      const result = await bridge.workspaceComponents.get({ workspaceId: nextWorkspaceId })
      if (!mountedRef.current || epoch !== componentsLoadEpochRef.current) return false
      if (surfaceScopeRef.current.workspaceId !== nextWorkspaceId) return false
      if (!result.ok) {
        setComponentsStatus('error')
        setComponentsNotice(errorMessage(result.error.code))
        return false
      }
      if (result.value.workspaceId !== nextWorkspaceId) {
        setComponentsStatus('error')
        setComponentsNotice('Workspace 组件身份不一致，已拒绝投影。')
        return false
      }
      applyComponentsSnapshot(result.value)
      setComponentsStatus('ready')
      return true
    },
    [applyComponentsSnapshot, bridge],
  )

  const beginComponentsOperation = useCallback(() => {
    if (componentsBusyRef.current) return null
    const nextWorkspaceId = surfaceScopeRef.current.workspaceId
    const snapshot = componentsSnapshotRef.current
    if (nextWorkspaceId === null || snapshot === null || snapshot.workspaceId !== nextWorkspaceId) {
      return null
    }
    componentsBusyRef.current = true
    setComponentsBusy(true)
    setComponentsNotice(null)
    return Object.freeze({
      epoch: ++componentsOperationEpochRef.current,
      workspaceId: nextWorkspaceId,
      snapshot,
    })
  }, [])

  const componentsOperationIsCurrent = useCallback(
    (epoch: number, nextWorkspaceId: string) =>
      mountedRef.current &&
      componentsOperationEpochRef.current === epoch &&
      surfaceScopeRef.current.workspaceId === nextWorkspaceId,
    [],
  )

  const finishComponentsOperation = useCallback((epoch: number) => {
    if (componentsOperationEpochRef.current !== epoch) return
    componentsBusyRef.current = false
    setComponentsBusy(false)
  }, [])

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

  const loadWorkspaceDirectory = useCallback(
    async (directoryPath: string, sourceState?: P7WorkspaceFilesState) => {
      const started = beginP7WorkspaceDirectoryList(
        sourceState ?? workspaceFilesRef.current,
        directoryPath,
      )
      if (started === null) return
      applyWorkspaceFilesState(started.state)
      const result = await bridge.workspaceFiles.list({
        workspaceId: started.request.workspaceId,
        authorizationGeneration: started.request.authorizationGeneration!,
        directoryPath: started.request.path,
      })
      if (!mountedRef.current) return
      const current = workspaceFilesRef.current
      const authorizationBefore = current.authorization
      const next = result.ok
        ? settleP7WorkspaceDirectoryList(current, started.request, result.value)
        : failP7WorkspaceDirectoryList(current, started.request, result.error.code)
      if (next === current) return
      applyWorkspaceFilesState(next)
      if (authorizationBefore !== null && next.authorization === null) {
        void releaseNativeWorkspaceFiles(authorizationBefore)
      }
      if (next.phase === 'error' && next.authorization === null) {
        onError(p7WorkspaceFileErrorMessage(next.errorCode))
        return
      }
      const directory = p7WorkspaceFileDirectory(next, directoryPath)
      if (directory?.status === 'error') {
        onError(p7WorkspaceFileErrorMessage(directory.errorCode))
      }
    },
    [applyWorkspaceFilesState, bridge, onError, releaseNativeWorkspaceFiles],
  )

  const authorizeWorkspaceFiles = useCallback(async () => {
    const current = workspaceFilesRef.current
    const previousAuthorization = current.authorization
    const started = beginP7WorkspaceFileAuthorization(current)
    if (started === null) return
    applyWorkspaceFilesState(started.state)
    onError(null)
    if (previousAuthorization !== null) {
      void releaseNativeWorkspaceFiles(previousAuthorization)
    }
    const result = await bridge.workspaceFiles.authorize({
      workspaceId: started.request.workspaceId,
    })
    if (!mountedRef.current) {
      if (result.ok) {
        const staleAuthorization = parseP7WorkspaceFileAuthorization(
          result.value,
          started.request.workspaceId,
        )
        if (staleAuthorization !== null) void releaseNativeWorkspaceFiles(staleAuthorization)
      }
      return
    }
    const latest = workspaceFilesRef.current
    const next = result.ok
      ? settleP7WorkspaceFileAuthorization(latest, started.request, result.value)
      : failP7WorkspaceFileAuthorization(latest, started.request, result.error.code)
    if (next === latest) {
      // The native picker may complete after a Workspace switch. The reducer
      // correctly refuses projection; release that late native binding too.
      if (result.ok) {
        const staleAuthorization = parseP7WorkspaceFileAuthorization(
          result.value,
          started.request.workspaceId,
        )
        if (staleAuthorization !== null) void releaseNativeWorkspaceFiles(staleAuthorization)
      }
      return
    }
    applyWorkspaceFilesState(next)
    if (next.authorization === null) {
      if (next.errorCode !== 'desktop_workspace_files_picker_cancelled') {
        onError(p7WorkspaceFileErrorMessage(next.errorCode))
      }
      return
    }
    pushOutput(`已授权本地目录：${next.authorization.rootName}`)
    void loadWorkspaceDirectory('', next)
  }, [
    applyWorkspaceFilesState,
    bridge,
    loadWorkspaceDirectory,
    onError,
    pushOutput,
    releaseNativeWorkspaceFiles,
  ])

  const releaseWorkspaceFiles = useCallback(() => {
    const released = releaseP7WorkspaceFilesAuthorization(workspaceFilesRef.current)
    applyWorkspaceFilesState(released.state)
    if (released.authorization !== null) {
      void releaseNativeWorkspaceFiles(released.authorization)
      pushOutput(`已释放本地目录授权：${released.authorization.rootName}`)
    }
  }, [applyWorkspaceFilesState, pushOutput, releaseNativeWorkspaceFiles])

  const toggleWorkspaceDirectory = useCallback(
    (directoryPath: string, expanded: boolean) => {
      const current = workspaceFilesRef.current
      const next = setP7WorkspaceDirectoryExpanded(current, directoryPath, expanded)
      applyWorkspaceFilesState(next)
      if (!expanded) return
      const directory = p7WorkspaceFileDirectory(next, directoryPath)
      if (directory === null || directory.status === 'idle' || directory.status === 'error') {
        void loadWorkspaceDirectory(directoryPath, next)
      }
    },
    [applyWorkspaceFilesState, loadWorkspaceDirectory],
  )

  const openWorkspaceFile = useCallback(
    async (path: string) => {
      const started = beginP7WorkspaceFileRead(workspaceFilesRef.current, path)
      if (started === null) {
        onError('文件路径不符合工作区边界。')
        return
      }
      applyWorkspaceFilesState(started.state)
      onError(null)
      const result = await bridge.workspaceFiles.read({
        workspaceId: started.request.workspaceId,
        authorizationGeneration: started.request.authorizationGeneration!,
        path: started.request.path,
      })
      if (!mountedRef.current) return
      const current = workspaceFilesRef.current
      const authorizationBefore = current.authorization
      const next = result.ok
        ? settleP7WorkspaceFileRead(current, started.request, result.value)
        : failP7WorkspaceFileRead(current, started.request, result.error.code)
      if (next === current) return
      applyWorkspaceFilesState(next)
      if (authorizationBefore !== null && next.authorization === null) {
        void releaseNativeWorkspaceFiles(authorizationBefore)
      }
      if (next.readPhase !== 'ready' || next.openFile === null) {
        onError(p7WorkspaceFileErrorMessage(next.errorCode))
        return
      }
      pushOutput(`已只读打开：${path}`)
    },
    [applyWorkspaceFilesState, bridge, onError, pushOutput, releaseNativeWorkspaceFiles],
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
      const authorization = workspaceFilesRef.current.authorization
      if (authorization !== null) void releaseNativeWorkspaceFiles(authorization)
      conversationSurfaceRef.current = unmountDesktopConversationSurface(
        conversationSurfaceRef.current,
      )
    }
  }, [releaseNativeWorkspaceFiles])

  useEffect(() => {
    void loadApplicationPreference()
  }, [loadApplicationPreference])

  useEffect(() => {
    if (workspaceId === null) {
      invalidateCompositionForWorkspace(null)
      return
    }
    void loadComposition(workspaceId)
  }, [invalidateCompositionForWorkspace, loadComposition, workspaceId])

  useEffect(() => {
    if (workspaceId === null) {
      invalidateComponentsForWorkspace(null)
      return
    }
    void loadComponents(workspaceId)
  }, [invalidateComponentsForWorkspace, loadComponents, workspaceId])

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

  const createCompositionProposal = async () => {
    const started = beginCompositionOperation()
    const desiredProfile = compositionDraft
    if (started === null || desiredProfile === null) return
    const result = await bridge.workspaceComposition.propose({
      workspaceId: started.workspaceId,
      expectedRevision: started.snapshot.profile.revision,
      expectedProfileSha256: started.snapshot.profile.profileSha256,
      desiredProfile: p7CloneCompositionProfile(desiredProfile),
    })
    if (!compositionOperationIsCurrent(started.epoch, started.workspaceId)) return
    if (!result.ok) {
      setCompositionNotice(errorMessage(result.error.code))
      finishCompositionOperation(started.epoch)
      return
    }
    const reloaded = await loadComposition(started.workspaceId)
    if (compositionOperationIsCurrent(started.epoch, started.workspaceId) && reloaded) {
      setCompositionNotice(
        result.value.replayed ? '既有相同提案已保留。' : '提案已创建，等待 Owner 审阅。',
      )
      pushOutput(`Workspace Profile 提案已创建：${result.value.proposal.id}`)
    }
    finishCompositionOperation(started.epoch)
  }

  const decideCompositionProposal = async (
    selectedProposal: DesktopWorkspaceCompositionProposal,
    decision: 'approve' | 'reject',
  ) => {
    const started = beginCompositionOperation()
    if (started === null) return
    const proposal = started.snapshot.proposals.find(
      (item) =>
        item.id === selectedProposal.id &&
        item.requestSha256 === selectedProposal.requestSha256 &&
        item.workspaceId === started.workspaceId &&
        item.decision === null,
    )
    if (proposal === undefined) {
      setCompositionNotice('提案身份已经变化，请重新读取后再处理。')
      finishCompositionOperation(started.epoch)
      return
    }
    const result = await bridge.workspaceComposition.decide({
      workspaceId: started.workspaceId,
      proposalId: proposal.id,
      requestSha256: proposal.requestSha256,
      decision,
    })
    if (!compositionOperationIsCurrent(started.epoch, started.workspaceId)) return
    if (!result.ok) {
      setCompositionNotice(errorMessage(result.error.code))
      finishCompositionOperation(started.epoch)
      return
    }
    const reloaded = await loadComposition(started.workspaceId)
    if (compositionOperationIsCurrent(started.epoch, started.workspaceId) && reloaded) {
      const message = decision === 'approve' ? '提案已批准并创建新修订。' : '提案已拒绝。'
      setCompositionNotice(message)
      pushOutput(`${message} ${proposal.id}`)
    }
    finishCompositionOperation(started.epoch)
  }

  const proposeCompositionRollback = async (targetRevision: number) => {
    const started = beginCompositionOperation()
    if (started === null) return
    const target = started.snapshot.revisions.find(
      (revision) =>
        revision.workspaceId === started.workspaceId && revision.revision === targetRevision,
    )
    if (target === undefined || target.revision === started.snapshot.profile.revision) {
      setCompositionNotice('回滚目标不属于当前 Workspace 历史。')
      finishCompositionOperation(started.epoch)
      return
    }
    const result = await bridge.workspaceComposition.proposeRollback({
      workspaceId: started.workspaceId,
      expectedRevision: started.snapshot.profile.revision,
      expectedProfileSha256: started.snapshot.profile.profileSha256,
      targetRevision: target.revision,
    })
    if (!compositionOperationIsCurrent(started.epoch, started.workspaceId)) return
    if (!result.ok) {
      setCompositionNotice(errorMessage(result.error.code))
      finishCompositionOperation(started.epoch)
      return
    }
    const reloaded = await loadComposition(started.workspaceId)
    if (compositionOperationIsCurrent(started.epoch, started.workspaceId) && reloaded) {
      setCompositionNotice(`已创建恢复到修订 ${target.revision} 的提案，尚未批准。`)
      pushOutput(`Workspace Profile 回滚提案已创建：${result.value.proposal.id}`)
    }
    finishCompositionOperation(started.epoch)
  }

  const requestAssistantComposition = async () => {
    const started = beginCompositionOperation()
    if (started === null) return
    const prompt = p7CompositionAssistantPrompt(compositionIntent, started.snapshot.profile.value)
    if (prompt === null) {
      setCompositionNotice('请输入 1–2000 字的 Workspace 调整意图。')
      finishCompositionOperation(started.epoch)
      return
    }
    if (desktopLiveSendBlocked(liveRef.current) || desktopTeamStopVisible(teamLiveRef.current)) {
      setCompositionNotice('当前仍有 Agent 运行，请结束后再生成设置提案。')
      finishCompositionOperation(started.epoch)
      return
    }

    const targetConversationId = await ensureConversation()
    if (
      targetConversationId === null ||
      !compositionOperationIsCurrent(started.epoch, started.workspaceId)
    ) {
      setCompositionNotice('无法建立用于生成提案的当前会话。')
      finishCompositionOperation(started.epoch)
      return
    }
    const baseline = await bridge.conversations.get({
      workspaceId: started.workspaceId,
      conversationId: targetConversationId,
    })
    if (!compositionOperationIsCurrent(started.epoch, started.workspaceId)) return
    if (!baseline.ok) {
      setCompositionNotice(errorMessage(baseline.error.code))
      finishCompositionOperation(started.epoch)
      return
    }
    const previousMessageIds = new Set(baseline.value.messages.map((message) => message.id))
    const completion = beginDesktopSurfaceDetailRequest(conversationSurfaceRef.current)
    conversationSurfaceRef.current = completion.surface
    const nextLive = beginDesktopLiveSend({
      ...liveRef.current,
      workspaceId: started.workspaceId,
      conversationId: targetConversationId,
    })
    if (nextLive.phase !== 'starting_identity') {
      setCompositionNotice('当前 Agent 调用状态不允许生成设置提案。')
      finishCompositionOperation(started.epoch)
      return
    }
    liveRef.current = nextLive
    setLive(nextLive)
    pushOutput('Agent 正在生成 Workspace Profile 提案')
    const sent = await bridge.conversations.send({
      workspaceId: started.workspaceId,
      conversationId: targetConversationId,
      content: prompt,
      sendEpoch: nextLive.sendEpoch,
    })
    const completed = completeDesktopLiveSend(liveRef.current, nextLive.sendGeneration)
    liveRef.current = completed
    setLive(completed)
    if (!compositionOperationIsCurrent(started.epoch, started.workspaceId)) return
    if (!sent.ok) {
      setCompositionNotice(errorMessage(sent.error.code))
      finishCompositionOperation(started.epoch)
      return
    }
    if (sent.value.type === 'cancelled') {
      setCompositionNotice('Agent 提案生成已停止。')
      finishCompositionOperation(started.epoch)
      return
    }
    const detail = await bridge.conversations.get({
      workspaceId: started.workspaceId,
      conversationId: targetConversationId,
    })
    if (!compositionOperationIsCurrent(started.epoch, started.workspaceId)) return
    if (!detail.ok) {
      setCompositionNotice(errorMessage(detail.error.code))
      finishCompositionOperation(started.epoch)
      return
    }
    const updatedConversations = conversationSurfaceRef.current.conversations.map((item) =>
      item.id === detail.value.conversation.id ? detail.value.conversation : item,
    )
    const applied = applyDesktopConversationCompletion(
      conversationSurfaceRef.current,
      completion.epoch,
      targetConversationId,
      detail.value.messages,
      updatedConversations,
    )
    conversationSurfaceRef.current = applied
    if (
      applied.mounted &&
      applied.detailRequestEpoch === completion.epoch &&
      applied.conversationId === targetConversationId
    ) {
      setMessages(applied.messages)
      setMessagesStatus(applied.messagesStatus)
      setMessagesError(null)
      setConversations(applied.conversations)
    }
    const assistantMessage = p7FindNewAssistantCompositionMessage(
      detail.value.messages,
      previousMessageIds,
    )
    if (assistantMessage === null) {
      setCompositionNotice('Agent 输出不是严格、完整的 Workspace Profile；未创建提案。')
      finishCompositionOperation(started.epoch)
      return
    }
    const proposalResult = await bridge.workspaceComposition.proposeFromAssistant({
      workspaceId: started.workspaceId,
      expectedRevision: started.snapshot.profile.revision,
      expectedProfileSha256: started.snapshot.profile.profileSha256,
      messageId: assistantMessage.id,
    })
    if (!compositionOperationIsCurrent(started.epoch, started.workspaceId)) return
    if (!proposalResult.ok) {
      setCompositionNotice(errorMessage(proposalResult.error.code))
      finishCompositionOperation(started.epoch)
      return
    }
    const reloaded = await loadComposition(started.workspaceId)
    if (compositionOperationIsCurrent(started.epoch, started.workspaceId) && reloaded) {
      setCompositionNotice('Agent 提案已生成，必须由 Owner 单独审阅并批准。')
      pushOutput(`Agent Workspace Profile 提案已创建：${proposalResult.value.proposal.id}`)
    }
    finishCompositionOperation(started.epoch)
  }

  const proposeWorkspaceComponent = async (
    catalog: DesktopWorkspaceComponentCatalogItem,
    action: DesktopWorkspaceComponentLifecycleAction,
    draft: P7WorkspaceComponentProposalDraft,
  ) => {
    const started = beginComponentsOperation()
    if (started === null) return
    const installation = started.snapshot.installations.find(
      (item) => item.componentId === catalog.componentId && item.state !== 'uninstalled',
    )
    const result = await bridge.workspaceComponents.propose({
      workspaceId: started.workspaceId,
      componentId: catalog.componentId,
      targetVersion: catalog.version,
      changeKind: action,
      expectedRevision: installation?.revision ?? 0,
      requestedGrants: draft.requestedGrants,
      desiredConfiguration: draft.desiredConfiguration,
      desiredSlotBindings: draft.desiredSlotBindings,
      dependencyGraph: draft.dependencyGraph,
      idempotencyKey: p7ComponentIdempotencyKey(`propose_${action}`),
    })
    if (!componentsOperationIsCurrent(started.epoch, started.workspaceId)) return
    if (!result.ok) {
      setComponentsNotice(errorMessage(result.error.code))
      finishComponentsOperation(started.epoch)
      return
    }
    const reloaded = await loadComponents(started.workspaceId)
    if (componentsOperationIsCurrent(started.epoch, started.workspaceId) && reloaded) {
      setComponentsNotice('组件提案已创建；必须由 Owner 审阅 exact SHA 后才能执行。')
      pushOutput(`Workspace 组件提案已创建：${result.value.proposal.proposalId}`)
    }
    finishComponentsOperation(started.epoch)
  }

  const importOwnerWorkspaceComponentPackage = async () => {
    const started = beginComponentsOperation()
    if (started === null) return
    const result = await bridge.workspaceComponents.importOwnerPackage({
      workspaceId: started.workspaceId,
    })
    if (!componentsOperationIsCurrent(started.epoch, started.workspaceId)) return
    if (!result.ok) {
      setComponentsNotice(errorMessage(result.error.code))
      finishComponentsOperation(started.epoch)
      return
    }
    if (result.value.cancelled || result.value.registration === null) {
      setComponentsNotice('未选择组件包；Catalog 未发生变化。')
      finishComponentsOperation(started.epoch)
      return
    }
    const reloaded = await loadComponents(started.workspaceId)
    if (componentsOperationIsCurrent(started.epoch, started.workspaceId) && reloaded) {
      const registration = result.value.registration
      setComponentsNotice(
        `组件包已登记：${registration.componentId} ${registration.version}；安装仍需单独提案与 Owner 批准。`,
      )
      pushOutput(`Workspace 组件包登记：${registration.componentId}@${registration.version}`)
    }
    finishComponentsOperation(started.epoch)
  }

  const requestAssistantDeclarativePackage = async () => {
    const started = beginComponentsOperation()
    if (started === null) return
    const prompt = p7AssistantDeclarativePackagePrompt(componentIntent)
    if (prompt === null) {
      setComponentsNotice('请输入 1–2000 字的声明式组件意图。')
      finishComponentsOperation(started.epoch)
      return
    }
    if (desktopLiveSendBlocked(liveRef.current) || desktopTeamStopVisible(teamLiveRef.current)) {
      setComponentsNotice('当前仍有 Agent 运行，请结束后再生成声明式组件包。')
      finishComponentsOperation(started.epoch)
      return
    }
    const targetConversationId = await ensureConversation()
    if (
      targetConversationId === null ||
      !componentsOperationIsCurrent(started.epoch, started.workspaceId)
    ) {
      setComponentsNotice('无法建立用于生成声明式组件包的当前会话。')
      finishComponentsOperation(started.epoch)
      return
    }
    const baseline = await bridge.conversations.get({
      workspaceId: started.workspaceId,
      conversationId: targetConversationId,
    })
    if (!componentsOperationIsCurrent(started.epoch, started.workspaceId)) return
    if (!baseline.ok) {
      setComponentsNotice(errorMessage(baseline.error.code))
      finishComponentsOperation(started.epoch)
      return
    }
    const previousMessageIds = new Set(baseline.value.messages.map((message) => message.id))
    const completion = beginDesktopSurfaceDetailRequest(conversationSurfaceRef.current)
    conversationSurfaceRef.current = completion.surface
    const nextLive = beginDesktopLiveSend({
      ...liveRef.current,
      workspaceId: started.workspaceId,
      conversationId: targetConversationId,
    })
    if (nextLive.phase !== 'starting_identity') {
      setComponentsNotice('当前 Agent 调用状态不允许生成声明式组件包。')
      finishComponentsOperation(started.epoch)
      return
    }
    liveRef.current = nextLive
    setLive(nextLive)
    setAssistantPackageReview(null)
    pushOutput('Agent 正在生成 Owner 待审声明式组件包')
    const sent = await bridge.conversations.send({
      workspaceId: started.workspaceId,
      conversationId: targetConversationId,
      content: prompt,
      sendEpoch: nextLive.sendEpoch,
    })
    const completed = completeDesktopLiveSend(liveRef.current, nextLive.sendGeneration)
    liveRef.current = completed
    setLive(completed)
    if (!componentsOperationIsCurrent(started.epoch, started.workspaceId)) return
    if (!sent.ok) {
      setComponentsNotice(errorMessage(sent.error.code))
      finishComponentsOperation(started.epoch)
      return
    }
    if (sent.value.type === 'cancelled') {
      setComponentsNotice('Agent 声明式组件包生成已停止。')
      finishComponentsOperation(started.epoch)
      return
    }
    const detail = await bridge.conversations.get({
      workspaceId: started.workspaceId,
      conversationId: targetConversationId,
    })
    if (!componentsOperationIsCurrent(started.epoch, started.workspaceId)) return
    if (!detail.ok) {
      setComponentsNotice(errorMessage(detail.error.code))
      finishComponentsOperation(started.epoch)
      return
    }
    const updatedConversations = conversationSurfaceRef.current.conversations.map((item) =>
      item.id === detail.value.conversation.id ? detail.value.conversation : item,
    )
    const applied = applyDesktopConversationCompletion(
      conversationSurfaceRef.current,
      completion.epoch,
      targetConversationId,
      detail.value.messages,
      updatedConversations,
    )
    conversationSurfaceRef.current = applied
    if (
      applied.mounted &&
      applied.detailRequestEpoch === completion.epoch &&
      applied.conversationId === targetConversationId
    ) {
      setMessages(applied.messages)
      setMessagesStatus(applied.messagesStatus)
      setMessagesError(null)
      setConversations(applied.conversations)
    }
    const assistantMessage = p7FindNewCompletedComponentAssistantMessage(
      detail.value.messages,
      previousMessageIds,
    )
    if (assistantMessage === null) {
      setComponentsNotice('没有找到新的成功 Agent 消息；未生成声明式组件包。')
      finishComponentsOperation(started.epoch)
      return
    }
    const review = await p7ParseAssistantDeclarativePackage({
      workspaceId: started.workspaceId,
      conversationId: targetConversationId,
      message: assistantMessage,
    })
    if (
      !componentsOperationIsCurrent(started.epoch, started.workspaceId) ||
      surfaceScopeRef.current.conversationId !== targetConversationId
    ) {
      finishComponentsOperation(started.epoch)
      return
    }
    if (review === null) {
      setComponentsNotice('Agent 输出不符合严格声明式组件包契约；未登记、未安装、未授权。')
      finishComponentsOperation(started.epoch)
      return
    }
    setAssistantPackageReview(review)
    setComponentsNotice('声明式组件包已解析；等待 Owner 核对完整 SHA 并明确登记。')
    pushOutput(`Agent 声明式组件包待审：${review.componentId}@${review.version}`)
    finishComponentsOperation(started.epoch)
  }

  const registerAssistantDeclarativePackage = async () => {
    const review = assistantPackageReview
    const started = beginComponentsOperation()
    if (started === null) return
    if (
      review === null ||
      review.workspaceId !== started.workspaceId ||
      review.conversationId !== surfaceScopeRef.current.conversationId
    ) {
      setComponentsNotice('待审组件包不属于当前 Workspace 与会话，已拒绝登记。')
      finishComponentsOperation(started.epoch)
      return
    }
    const result = await bridge.workspaceComponents.importAssistantPackage({
      workspaceId: review.workspaceId,
      conversationId: review.conversationId,
      messageId: review.messageId,
      packageJson: review.packageJson,
      manifestSha256: review.manifestSha256,
      packageSha256: review.packageSha256,
    })
    if (!componentsOperationIsCurrent(started.epoch, started.workspaceId)) return
    if (!result.ok) {
      setComponentsNotice(errorMessage(result.error.code))
      finishComponentsOperation(started.epoch)
      return
    }
    if (result.value.cancelled || result.value.registration === null) {
      setComponentsNotice('组件包登记未完成；Catalog 未发生变化。')
      finishComponentsOperation(started.epoch)
      return
    }
    setAssistantPackageReview(null)
    const reloaded = await loadComponents(started.workspaceId)
    if (componentsOperationIsCurrent(started.epoch, started.workspaceId) && reloaded) {
      const registration = result.value.registration
      setComponentsNotice(
        `Agent 组件包已登记：${registration.componentId} ${registration.version}；安装、授权和激活仍需独立 Owner 提案。`,
      )
      pushOutput(`Agent Workspace 组件包登记：${registration.componentId}@${registration.version}`)
    }
    finishComponentsOperation(started.epoch)
  }

  const discardAssistantDeclarativePackage = () => {
    if (componentsBusyRef.current || assistantPackageReview === null) return
    setAssistantPackageReview(null)
    setComponentsNotice('已丢弃 Agent 声明式组件包；Catalog 未发生变化。')
  }

  const requestAssistantWorkspaceComponent = async () => {
    const started = beginComponentsOperation()
    if (started === null) return
    const prompt = p7WorkspaceComponentAssistantPrompt(componentIntent, started.snapshot)
    if (prompt === null) {
      setComponentsNotice('请输入 1–2000 字的组件调整意图，并确保存在已登记的可用组件包。')
      finishComponentsOperation(started.epoch)
      return
    }
    if (desktopLiveSendBlocked(liveRef.current) || desktopTeamStopVisible(teamLiveRef.current)) {
      setComponentsNotice('当前仍有 Agent 运行，请结束后再生成组件提案。')
      finishComponentsOperation(started.epoch)
      return
    }
    const targetConversationId = await ensureConversation()
    if (
      targetConversationId === null ||
      !componentsOperationIsCurrent(started.epoch, started.workspaceId)
    ) {
      setComponentsNotice('无法建立用于生成组件提案的当前会话。')
      finishComponentsOperation(started.epoch)
      return
    }
    const baseline = await bridge.conversations.get({
      workspaceId: started.workspaceId,
      conversationId: targetConversationId,
    })
    if (!componentsOperationIsCurrent(started.epoch, started.workspaceId)) return
    if (!baseline.ok) {
      setComponentsNotice(errorMessage(baseline.error.code))
      finishComponentsOperation(started.epoch)
      return
    }
    const previousMessageIds = new Set(baseline.value.messages.map((message) => message.id))
    const completion = beginDesktopSurfaceDetailRequest(conversationSurfaceRef.current)
    conversationSurfaceRef.current = completion.surface
    const nextLive = beginDesktopLiveSend({
      ...liveRef.current,
      workspaceId: started.workspaceId,
      conversationId: targetConversationId,
    })
    if (nextLive.phase !== 'starting_identity') {
      setComponentsNotice('当前 Agent 调用状态不允许生成组件提案。')
      finishComponentsOperation(started.epoch)
      return
    }
    liveRef.current = nextLive
    setLive(nextLive)
    pushOutput('Agent 正在生成 Workspace 组件提案')
    const sent = await bridge.conversations.send({
      workspaceId: started.workspaceId,
      conversationId: targetConversationId,
      content: prompt,
      sendEpoch: nextLive.sendEpoch,
    })
    const completed = completeDesktopLiveSend(liveRef.current, nextLive.sendGeneration)
    liveRef.current = completed
    setLive(completed)
    if (!componentsOperationIsCurrent(started.epoch, started.workspaceId)) return
    if (!sent.ok) {
      setComponentsNotice(errorMessage(sent.error.code))
      finishComponentsOperation(started.epoch)
      return
    }
    if (sent.value.type === 'cancelled') {
      setComponentsNotice('Agent 组件提案生成已停止。')
      finishComponentsOperation(started.epoch)
      return
    }
    const detail = await bridge.conversations.get({
      workspaceId: started.workspaceId,
      conversationId: targetConversationId,
    })
    if (!componentsOperationIsCurrent(started.epoch, started.workspaceId)) return
    if (!detail.ok) {
      setComponentsNotice(errorMessage(detail.error.code))
      finishComponentsOperation(started.epoch)
      return
    }
    const updatedConversations = conversationSurfaceRef.current.conversations.map((item) =>
      item.id === detail.value.conversation.id ? detail.value.conversation : item,
    )
    const applied = applyDesktopConversationCompletion(
      conversationSurfaceRef.current,
      completion.epoch,
      targetConversationId,
      detail.value.messages,
      updatedConversations,
    )
    conversationSurfaceRef.current = applied
    if (
      applied.mounted &&
      applied.detailRequestEpoch === completion.epoch &&
      applied.conversationId === targetConversationId
    ) {
      setMessages(applied.messages)
      setMessagesStatus(applied.messagesStatus)
      setMessagesError(null)
      setConversations(applied.conversations)
    }
    const assistantMessage = p7FindNewCompletedComponentAssistantMessage(
      detail.value.messages,
      previousMessageIds,
    )
    if (assistantMessage === null) {
      setComponentsNotice('没有找到新的、已完成且身份一致的 Agent 消息；未创建组件提案。')
      finishComponentsOperation(started.epoch)
      return
    }
    const proposalResult = await bridge.workspaceComponents.proposeFromAssistant({
      workspaceId: started.workspaceId,
      messageId: assistantMessage.id,
      idempotencyKey: p7ComponentIdempotencyKey('assistant_proposal'),
    })
    if (!componentsOperationIsCurrent(started.epoch, started.workspaceId)) return
    if (!proposalResult.ok) {
      setComponentsNotice(errorMessage(proposalResult.error.code))
      finishComponentsOperation(started.epoch)
      return
    }
    const reloaded = await loadComponents(started.workspaceId)
    if (componentsOperationIsCurrent(started.epoch, started.workspaceId) && reloaded) {
      setComponentsNotice('Agent 组件提案已生成；只能由 Owner 单独审阅并批准。')
      pushOutput(`Agent Workspace 组件提案已创建：${proposalResult.value.proposal.proposalId}`)
    }
    finishComponentsOperation(started.epoch)
  }

  const decideWorkspaceComponent = async (
    proposal: DesktopWorkspaceComponentProposal,
    decision: 'approve' | 'reject',
  ) => {
    const started = beginComponentsOperation()
    if (started === null || proposal.workspaceId !== started.workspaceId) return
    const result = await bridge.workspaceComponents.decide({
      workspaceId: started.workspaceId,
      proposalId: proposal.proposalId,
      decision,
      requestSha256: proposal.requestSha256,
    })
    if (!componentsOperationIsCurrent(started.epoch, started.workspaceId)) return
    if (!result.ok) {
      setComponentsNotice(errorMessage(result.error.code))
      finishComponentsOperation(started.epoch)
      return
    }
    const reloaded = await loadComponents(started.workspaceId)
    if (componentsOperationIsCurrent(started.epoch, started.workspaceId) && reloaded) {
      setComponentsNotice(
        decision === 'approve' ? '组件提案已批准，等待 Owner 执行。' : '组件提案已拒绝。',
      )
      pushOutput(
        `Workspace 组件提案${decision === 'approve' ? '已批准' : '已拒绝'}：${proposal.proposalId}`,
      )
    }
    finishComponentsOperation(started.epoch)
  }

  const executeWorkspaceComponentAction = async (proposal: DesktopWorkspaceComponentProposal) => {
    const started = beginComponentsOperation()
    if (
      started === null ||
      proposal.workspaceId !== started.workspaceId ||
      proposal.decision !== 'approved'
    ) {
      return
    }
    const result = await bridge.workspaceComponents.action({
      workspaceId: started.workspaceId,
      componentId: proposal.componentId,
      action: proposal.changeKind,
      proposalId: proposal.proposalId,
      requestSha256: proposal.requestSha256,
      expectedRevision: proposal.baseRevision,
      manifestSha256: proposal.manifestSha256,
      packageSha256: proposal.packageSha256,
      idempotencyKey: p7ComponentIdempotencyKey(`action_${proposal.changeKind}`),
    })
    if (!componentsOperationIsCurrent(started.epoch, started.workspaceId)) return
    if (!result.ok) {
      setComponentsNotice(errorMessage(result.error.code))
      finishComponentsOperation(started.epoch)
      return
    }
    const reloaded = await loadComponents(started.workspaceId)
    if (componentsOperationIsCurrent(started.epoch, started.workspaceId) && reloaded) {
      setComponentsNotice(
        `组件生命周期操作已落地：${result.value.installation?.state ?? result.value.operation.state}`,
      )
      pushOutput(`Workspace 组件 ${proposal.changeKind}：${proposal.componentId}`)
    }
    finishComponentsOperation(started.epoch)
  }

  const invokeWorkspaceComponent = async (
    installation: DesktopWorkspaceComponentInstallation,
    request: P7WorkspaceComponentInvokeRequest,
  ) => {
    const operation = request.operation
    const started = beginComponentsOperation()
    if (
      started === null ||
      installation.workspaceId !== started.workspaceId ||
      installation.state !== 'active' ||
      installation.health !== 'healthy'
    ) {
      return
    }
    const catalog = started.snapshot.catalog.find(
      (item) =>
        item.componentId === installation.componentId && item.version === installation.version,
    )
    if (catalog === undefined || !catalog.operations.includes(operation)) {
      setComponentsNotice('组件 operation 与当前受信 catalog 不一致。')
      finishComponentsOperation(started.epoch)
      return
    }
    const base = {
      workspaceId: started.workspaceId,
      componentId: installation.componentId,
      expectedRevision: installation.revision,
      bindingGeneration: installation.bindingGeneration,
      manifestSha256: installation.manifestSha256,
      packageSha256: installation.packageSha256,
      idempotencyKey: p7ComponentIdempotencyKey('invoke'),
      bytesOutReserved: Math.min(catalog.budgets.maxBytesOut, 4_194_304),
      tokensReserved: Math.min(catalog.budgets.maxTokens, 131_072),
      wallTimeMs: Math.min(catalog.budgets.maxWallTimeMs, 600_000),
      costUnits: Math.min(catalog.budgets.maxCostUnits, 1_000),
    } as const
    const input = { ...base, ...request }
    const result = await bridge.workspaceComponents.invoke(input)
    if (!componentsOperationIsCurrent(started.epoch, started.workspaceId)) return
    if (!result.ok) {
      setComponentsNotice(errorMessage(result.error.code))
      finishComponentsOperation(started.epoch)
      return
    }
    const reloaded = await loadComponents(started.workspaceId)
    if (componentsOperationIsCurrent(started.epoch, started.workspaceId) && reloaded) {
      const uiBinding =
        request.operation === 'ui.render'
          ? installation.currentSlotBindings.find(
              (binding) => binding.slotId === request.arguments.slotId,
            )
          : undefined
      const nextSurface = p7SetWorkspaceComponentSurface(componentSurfaceStateRef.current, {
        workspaceId: started.workspaceId,
        componentId: installation.componentId,
        operationId: result.value.operationId,
        operation,
        state: result.value.state,
        output: result.value.output,
        bindingGeneration: installation.bindingGeneration,
        ...(request.operation === 'ui.render'
          ? {
              ...(p7WorkspaceComponentHostSlotId(request.arguments.slotId)
                ? { slotId: request.arguments.slotId }
                : {}),
              bindingKey: uiBinding?.bindingKey ?? request.arguments.viewId,
              orderIndex: uiBinding?.orderIndex ?? 0,
            }
          : {}),
      })
      componentSurfaceStateRef.current = nextSurface
      setComponentSurfaceState(nextSurface)
      const openedEntry = nextSurface.entries.find(
        (entry) => entry.surface?.operationId === result.value.operationId,
      )
      setComponentsNotice(
        openedEntry?.surface === null || openedEntry === undefined
          ? '组件输出未进入编辑区；标准工作台保持可用。'
          : '组件调用结果已在工作台中打开。',
      )
      if (openedEntry?.surface !== null && openedEntry?.surface !== undefined) {
        appendEvent(p7WorkspaceComponentResultEventLogLine(openedEntry.surface))
      }
      pushOutput(`Workspace 组件调用 ${operation}：${result.value.state}`)
    }
    finishComponentsOperation(started.epoch)
  }

  const emergencyStopWorkspaceComponents = async () => {
    const started = beginComponentsOperation()
    if (started === null) return
    const result = await bridge.workspaceComponents.emergencyStop({
      workspaceId: started.workspaceId,
      idempotencyKey: p7ComponentIdempotencyKey('emergency_stop'),
      reasonCode: 'owner_emergency_stop',
    })
    if (!componentsOperationIsCurrent(started.epoch, started.workspaceId)) return
    if (!result.ok) {
      setComponentsNotice(errorMessage(result.error.code))
      finishComponentsOperation(started.epoch)
      return
    }
    const reloaded = await loadComponents(started.workspaceId)
    if (componentsOperationIsCurrent(started.epoch, started.workspaceId) && reloaded) {
      const safeState = p7EnterWorkspaceComponentSafeMode(started.workspaceId, 'emergency-stop')
      componentSurfaceStateRef.current = safeState
      componentSurfaceInFlightRef.current.clear()
      setComponentSurfaceState(safeState)
      setComponentsNotice(`紧急停止已完成：${result.value.stoppedComponentIds.length} 个组件。`)
      pushOutput(`Workspace 组件紧急停止：${result.value.stoppedComponentIds.length} 个组件`)
    }
    finishComponentsOperation(started.epoch)
  }

  const reconcileWorkspaceComponent = async (
    effect: DesktopWorkspaceComponentEffect,
    outcome: 'succeeded' | 'failed',
    evidenceSha256: string,
  ) => {
    const started = beginComponentsOperation()
    if (started === null || effect.workspaceId !== started.workspaceId) return
    const operation = started.snapshot.operations.find(
      (item) => item.operationId === effect.operationId,
    )
    if (operation === undefined || !/^[a-f0-9]{64}$/.test(evidenceSha256)) {
      setComponentsNotice('Reconciliation 需要匹配的 operation 与 64 位 evidence SHA-256。')
      finishComponentsOperation(started.epoch)
      return
    }
    const result = await bridge.workspaceComponents.reconcile({
      workspaceId: started.workspaceId,
      operationId: operation.operationId,
      effectId: effect.effectId,
      requestSha256: operation.requestSha256,
      outcome,
      evidenceSha256,
    })
    if (!componentsOperationIsCurrent(started.epoch, started.workspaceId)) return
    if (!result.ok) {
      setComponentsNotice(errorMessage(result.error.code))
      finishComponentsOperation(started.epoch)
      return
    }
    const reloaded = await loadComponents(started.workspaceId)
    if (componentsOperationIsCurrent(started.epoch, started.workspaceId) && reloaded) {
      setComponentsNotice(`Reconciliation 已记录：${outcome}`)
      pushOutput(`Workspace 组件 reconciliation：${result.value.reconciliationId}`)
    }
    finishComponentsOperation(started.epoch)
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
    invalidateCompositionForWorkspace(result.value.workspace.id)
    invalidateComponentsForWorkspace(result.value.workspace.id)
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
    if (!p7WorkspaceSelectionChangesScope(surfaceScopeRef.current.workspaceId, nextWorkspaceId)) {
      return
    }
    // Invalidate the run history synchronously with the scope switch: the
    // first frame after the switch must not render or allow clicks on the
    // previous workspace's runs.
    runHistoryEpochRef.current += 1
    setRunHistory([])
    setRunHistoryStatus('loading')
    setRunHistoryWorkspaceId(nextWorkspaceId)
    invalidateCompositionForWorkspace(nextWorkspaceId)
    invalidateComponentsForWorkspace(nextWorkspaceId)
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

  useEffect(() => {
    if (
      workspaceId === null ||
      componentsWorkspaceId !== workspaceId ||
      componentsStatus !== 'ready' ||
      componentsSnapshot?.workspaceId !== workspaceId
    ) {
      return
    }
    const snapshot = componentsSnapshot
    const bindings = p7WorkspaceComponentCommittedUiBindings(snapshot)
    const reconciled = p7ReconcileWorkspaceComponentSurfaces(
      componentSurfaceStateRef.current,
      workspaceId,
      bindings,
    )
    if (reconciled !== componentSurfaceStateRef.current) {
      componentSurfaceStateRef.current = reconciled
      setComponentSurfaceState(reconciled)
    }
    const requests = p7WorkspaceComponentSurfaceRequests(reconciled, snapshot)
    for (const binding of requests) {
      if (componentSurfaceInFlightRef.current.has(binding.key)) continue
      componentSurfaceInFlightRef.current.add(binding.key)
      void (async () => {
        const result = await bridge.workspaceComponents
          .invoke({
            workspaceId: binding.workspaceId,
            componentId: binding.componentId,
            expectedRevision: binding.installationRevision,
            bindingGeneration: binding.bindingGeneration,
            manifestSha256: binding.manifestSha256,
            packageSha256: binding.packageSha256,
            idempotencyKey: p7ComponentIdempotencyKey('reconstruct_ui'),
            bytesOutReserved: Math.min(binding.budgets.maxBytesOut, 4_194_304),
            tokensReserved: Math.min(binding.budgets.maxTokens, 131_072),
            wallTimeMs: Math.min(binding.budgets.maxWallTimeMs, 600_000),
            costUnits: Math.min(binding.budgets.maxCostUnits, 1_000),
            operation: 'ui.render',
            arguments: { slotId: binding.slotId, viewId: binding.componentId },
          })
          .catch(() => null)
        if (!mountedRef.current || surfaceScopeRef.current.workspaceId !== binding.workspaceId) {
          return
        }
        const latestSnapshot = componentsSnapshotRef.current
        if (
          latestSnapshot?.workspaceId !== binding.workspaceId ||
          !p7WorkspaceComponentCommittedUiBindings(latestSnapshot).some(
            (candidate) => candidate.key === binding.key,
          )
        ) {
          return
        }
        const succeeded = result !== null && result.ok
        const nextSurface = p7SetWorkspaceComponentSurface(componentSurfaceStateRef.current, {
          workspaceId: binding.workspaceId,
          componentId: binding.componentId,
          operationId: succeeded ? result.value.operationId : `reconstruct:${binding.key}`,
          operation: 'ui.render',
          state: succeeded ? result.value.state : 'failed',
          output: succeeded ? result.value.output : null,
          bindingGeneration: binding.bindingGeneration,
          slotId: binding.slotId,
          bindingKey: binding.bindingKey,
          orderIndex: binding.orderIndex,
        })
        componentSurfaceStateRef.current = nextSurface
        setComponentSurfaceState(nextSurface)
        const entry = nextSurface.entries.find((candidate) => candidate.key === binding.key)
        if (entry?.surface !== null && entry?.surface !== undefined) {
          appendEvent(p7WorkspaceComponentResultEventLogLine(entry.surface))
        }
        pushOutput(
          `Workspace UI 重建 ${binding.componentId}/${binding.bindingKey}：${
            succeeded ? result.value.state : 'failed'
          }`,
        )
      })().finally(() => {
        componentSurfaceInFlightRef.current.delete(binding.key)
      })
    }
  }, [
    appendEvent,
    bridge,
    componentSurfaceState,
    componentsSnapshot,
    componentsStatus,
    componentsWorkspaceId,
    pushOutput,
    workspaceId,
  ])

  // The live slot only applies while the user views the live run's origin
  // conversation; elsewhere the brief falls back to the history selection,
  // which itself is conversation-scoped when auto-followed.
  const compositionView = p7CompositionProjection({
    loadedWorkspaceId: compositionWorkspaceId,
    viewWorkspaceId: workspaceId,
    status: compositionStatus,
    snapshot: compositionSnapshot,
  })
  const compositionDraftView = compositionView.snapshot === null ? null : compositionDraft
  const componentsView = p7WorkspaceComponentsProjection({
    loadedWorkspaceId: componentsWorkspaceId,
    viewWorkspaceId: workspaceId,
    status: componentsStatus,
    snapshot: componentsSnapshot,
  })
  const componentSurface = p7WorkspaceComponentSurfaceProjection({
    state: componentSurfaceState,
    viewWorkspaceId: workspaceId,
    activeComponentIds:
      componentsView.snapshot?.installations
        .filter((installation) => installation.state === 'active')
        .map((installation) => installation.componentId) ?? [],
  })
  const selectedWorkspaceName =
    workspaces.find((workspace) => workspace.id === workspaceId)?.name ?? '未选择工作空间'
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
      workspaceName={selectedWorkspaceName}
      applicationPreference={applicationPreference}
      applicationPreferenceStatus={applicationPreferenceStatus}
      onApplicationPreferenceChange={(density, reduceMotion) =>
        void updateApplicationPreference(density, reduceMotion)
      }
      compositionStatus={compositionView.status}
      compositionSnapshot={compositionView.snapshot}
      compositionDraft={compositionDraftView}
      onCompositionDraftChange={(profile) =>
        setCompositionDraft(p7CloneCompositionProfile(profile))
      }
      onCreateCompositionProposal={() => void createCompositionProposal()}
      onRequestAssistantComposition={() => void requestAssistantComposition()}
      compositionIntent={compositionIntent}
      onCompositionIntentChange={setCompositionIntent}
      onDecideCompositionProposal={(proposal, decision) =>
        void decideCompositionProposal(proposal, decision)
      }
      onProposeCompositionRollback={(targetRevision) =>
        void proposeCompositionRollback(targetRevision)
      }
      compositionBusy={compositionBusy}
      compositionNotice={compositionNotice}
      status={componentsView.status}
      snapshot={componentsView.snapshot}
      busy={componentsBusy}
      notice={componentsNotice}
      assistantIntent={componentIntent}
      onAssistantIntentChange={setComponentIntent}
      assistantPackageReview={
        assistantPackageReview?.workspaceId === workspaceId &&
        assistantPackageReview.conversationId === conversationId
          ? assistantPackageReview
          : null
      }
      onRequestAssistantPackage={() => void requestAssistantDeclarativePackage()}
      onRegisterAssistantPackage={() => void registerAssistantDeclarativePackage()}
      onDiscardAssistantPackage={discardAssistantDeclarativePackage}
      onRequestAssistantProposal={() => void requestAssistantWorkspaceComponent()}
      onImportOwnerPackage={() => void importOwnerWorkspaceComponentPackage()}
      onPropose={(catalog, action, draft) => void proposeWorkspaceComponent(catalog, action, draft)}
      onDecide={(proposal, decision) => void decideWorkspaceComponent(proposal, decision)}
      onAction={(proposal) => void executeWorkspaceComponentAction(proposal)}
      onInvoke={(installation, request) => void invokeWorkspaceComponent(installation, request)}
      onEmergencyStop={() => void emergencyStopWorkspaceComponents()}
      onReconcile={(effect, outcome, evidenceSha256) =>
        void reconcileWorkspaceComponent(effect, outcome, evidenceSha256)
      }
      workspaceFilesAuthorized={p7WorkspaceFilesAuthorized(workspaceFiles)}
      workspaceFiles={workspaceFiles}
      componentSurface={componentSurface}
      onAuthorizeWorkspaceFiles={() => void authorizeWorkspaceFiles()}
      onReleaseWorkspaceFiles={releaseWorkspaceFiles}
      onToggleWorkspaceDirectory={toggleWorkspaceDirectory}
      onOpenWorkspaceFile={(path) => void openWorkspaceFile(path)}
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
