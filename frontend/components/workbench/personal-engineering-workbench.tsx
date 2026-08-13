'use client'

import { useEffect, useMemo, useRef, useState } from 'react'
import useSWR from 'swr'
import {
  Archive,
  Bot,
  BrainCircuit,
  CircleDot,
  Cog,
  FileText,
  Folder,
  History,
  MessageSquarePlus,
  Pin,
  RotateCcw,
  Search,
  Send,
  ShieldCheck,
  Sparkles,
  Square,
  User,
  Users,
  X,
} from 'lucide-react'
import { toast } from 'sonner'
import {
  agentAlphaApi,
  documentsApi,
  getApiErrorMessage,
  userSettingsApi,
  workspacesApi,
  type AgentAlphaProfile,
} from '@/lib/api'
import { consumeAgentAlphaStream, type AgentAlphaUsage } from '@/lib/agent-alpha-stream'
import { isUserCancelledError } from '@/lib/cancel-detection'
import { useAuth } from '@/lib/hooks/use-auth'
import { InvocationGuard, type InvocationPhase } from '@/lib/invocation-state'
import {
  advanceModelSettingsScope,
  captureModelSettingPreparation,
  modelSettingPreparationIsCurrent,
  modelSettingsScopeKey,
  projectionForScope,
  type ModelSettingsProjection,
  type ModelSettingsScope,
} from '@/lib/model-settings-projection'
import { agentInvokeConditionsMet, canInvokeAgent } from '@/lib/personal-runtime-gate'
import {
  PERSONAL_EMPLOYEES,
  P6_AGENT_ALPHA_MAX_MESSAGE_CHARACTERS,
  P6_WORKBENCH_STORAGE_KEY,
  appendWorkbenchMessage,
  appendWorkbenchTimelineEvent,
  createInitialWorkbenchState,
  estimateSessionTokens,
  listWorkbenchSessions,
  parseEmployeeInvocation,
  parseWorkbenchState,
  prepareEmployeeRoleMessage,
  prepareWorkbenchStateForPersistence,
  renameSession,
  sanitizeWorkbenchPersistenceText,
  setActiveSession,
  setSessionArchived,
  setSessionPinned,
  tryAddSession,
  type EmployeeDefinition,
  type EmployeeId,
  type WorkbenchSession,
  type WorkbenchState,
} from '@/lib/p6-workbench'
import {
  P6_GEAR_PROFILES,
  buildP6AdaptationInstruction,
  estimateP6Cost,
  getP6ProviderProfile,
  type P6ModelIdentity,
  type P6ReasoningGear,
} from '@/lib/p6-model-profiles'
import {
  WorkspaceFilePanel,
  type P6TaskBinding,
  type WorkspaceFilePanelHandle,
} from '@/components/workbench/workspace-file-panel'
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
import { cn } from '@/lib/utils'
import type {
  AgentModelSettingRead,
  P6EmployeeRoleId,
  ProviderCredentialRead,
  WorkspaceRead,
} from '@/lib/types'

type RuntimePosture = Awaited<ReturnType<typeof agentAlphaApi.status>>

interface InvocationContext {
  readonly generation: number
  readonly sessionId: string
  readonly workspaceId: string
  readonly agentVersionId: string
  invocationId: string | null
  taskId: string | null
  cancellationIdentityUnavailable: boolean
}

type WorkbenchInvocationPhase = InvocationPhase | 'preparing'

function localKey(tenantId?: string, userId?: string): string {
  return `${P6_WORKBENCH_STORAGE_KEY}:${tenantId ?? 'anonymous'}:${userId ?? 'anonymous'}`
}

function employeeById(id: EmployeeId | null): EmployeeDefinition {
  return PERSONAL_EMPLOYEES.find((employee) => employee.id === id) ?? PERSONAL_EMPLOYEES[0]!
}

function shortTime(value: string): string {
  const date = new Date(value)
  return date.toDateString() === new Date().toDateString()
    ? date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
    : date.toLocaleDateString([], { month: '2-digit', day: '2-digit' })
}

export function PersonalEngineeringWorkbench() {
  const { tenant, user, isAuthenticated, bootstrapStatus } = useAuth()
  const storageKey = localKey(tenant?.id, user?.id)
  const [state, setState] = useState<WorkbenchState>(() => createInitialWorkbenchState())
  const [hydratedKey, setHydratedKey] = useState<string | null>(null)
  const [query, setQuery] = useState('')
  const [archiveMode, setArchiveMode] = useState(false)
  const [timelineOpen, setTimelineOpen] = useState(false)
  const [input, setInput] = useState('')
  const [streaming, setStreaming] = useState('')
  const [phase, setPhase] = useState<InvocationPhase>('idle')
  const [preparing, setPreparing] = useState(false)
  const [activeEmployeeId, setActiveEmployeeId] = useState<EmployeeId>('parent')
  const [workspaceId, setWorkspaceId] = useState('')
  const [profiles, setProfiles] = useState<AgentAlphaProfile[]>([])
  const [agentVersionId, setAgentVersionId] = useState('')
  const [posture, setPosture] = useState<RuntimePosture | null>(null)
  const [runtimeError, setRuntimeError] = useState<string | null>(null)
  const [identity, setIdentity] = useState('尚未调用模型')
  const [modelIdentity, setModelIdentity] = useState<P6ModelIdentity>({
    providerId: null,
    modelId: null,
  })
  const [gear, setGear] = useState<P6ReasoningGear>('standard')
  const [usage, setUsage] = useState<AgentAlphaUsage | null>(null)
  const [taskBinding, setTaskBinding] = useState<P6TaskBinding | null>(null)
  const [fileMutating, setFileMutating] = useState(false)
  const [modelSettingsProjection, setModelSettingsProjection] =
    useState<ModelSettingsProjection | null>(null)
  const [credentials, setCredentials] = useState<ProviderCredentialRead[]>([])
  const [modelEditorRole, setModelEditorRole] = useState<P6EmployeeRoleId | null>(null)
  const [modelEditorCredential, setModelEditorCredential] = useState('')
  const [modelEditorName, setModelEditorName] = useState('')
  const [savingModelSetting, setSavingModelSetting] = useState(false)
  const [testingModelSetting, setTestingModelSetting] = useState(false)
  const guardRef = useRef(new InvocationGuard())
  const submitInFlightRef = useRef(false)
  const filePanelRef = useRef<WorkspaceFilePanelHandle>(null)
  const invocationContextRef = useRef<InvocationContext | null>(null)
  const runtimeRequestGenerationRef = useRef(0)
  const persistenceWarningKeyRef = useRef<string | null>(null)
  const modelSettingsScopeRef = useRef<ModelSettingsScope>({ key: null, generation: 0 })
  const preparationScopeRef = useRef({
    storageKey,
    sessionId: '',
    workspaceId,
    agentVersionId,
    gear,
  })
  const identityReady =
    bootstrapStatus === 'ready' && isAuthenticated && Boolean(tenant?.id && user?.id)

  const { data: workspaces, error: workspaceError } = useSWR(
    identityReady ? ['p6-workspaces', tenant!.id, user!.id] : null,
    () => workspacesApi.list(),
  )
  const { data: documents } = useSWR(
    identityReady ? ['p6-documents', tenant!.id, user!.id] : null,
    () => documentsApi.list({ limit: 8 }),
  )
  const modelSettingsKey = identityReady ? modelSettingsScopeKey(workspaceId, agentVersionId) : null
  modelSettingsScopeRef.current = advanceModelSettingsScope(
    modelSettingsScopeRef.current,
    modelSettingsKey,
  )
  const modelSettingsScope = modelSettingsScopeRef.current
  const currentModelSettingsProjection = projectionForScope(
    modelSettingsProjection,
    modelSettingsScope,
  )
  const modelSettings = currentModelSettingsProjection?.items ?? []

  useEffect(() => {
    let alive = true
    const requestedScope = modelSettingsScopeRef.current
    if (!modelSettingsKey) {
      setModelSettingsProjection(null)
      return () => {
        alive = false
      }
    }
    void Promise.all([
      agentAlphaApi.modelSettings(workspaceId, agentVersionId),
      userSettingsApi.credentials(),
    ])
      .then(([settings, credentialList]) => {
        if (
          !alive ||
          modelSettingsScopeRef.current.key !== requestedScope.key ||
          modelSettingsScopeRef.current.generation !== requestedScope.generation
        ) {
          return
        }
        setModelSettingsProjection({ scope: requestedScope, items: settings.items })
        setCredentials(credentialList.items.filter((item) => item.is_active && !item.revoked_at))
      })
      .catch((reason) => {
        if (alive) {
          if (
            modelSettingsScopeRef.current.key !== requestedScope.key ||
            modelSettingsScopeRef.current.generation !== requestedScope.generation
          ) {
            return
          }
          setModelSettingsProjection(null)
          toast.error('员工模型配置加载失败', {
            description: getApiErrorMessage(reason, 'p6_model_settings_failed'),
          })
        }
      })
    return () => {
      alive = false
    }
  }, [agentVersionId, modelSettingsKey, workspaceId])
  const modelSettingsReady = currentModelSettingsProjection !== null

  const activeSession =
    state.sessions.find((session) => session.id === state.activeSessionId) ?? state.sessions[0]
  preparationScopeRef.current = {
    storageKey,
    sessionId: activeSession?.id ?? '',
    workspaceId,
    agentVersionId,
    gear,
  }

  useEffect(() => {
    guardRef.current.invalidate()
    invocationContextRef.current = null
    submitInFlightRef.current = false
    setPreparing(false)
    setPhase('idle')
    setStreaming('')
    setUsage(null)
    runtimeRequestGenerationRef.current += 1
    setWorkspaceId('')
    setProfiles([])
    setAgentVersionId('')
    setPosture(null)
    setRuntimeError(null)
    setTaskBinding(null)
    setModelIdentity({ providerId: null, modelId: null })
    setIdentity('尚未调用模型')
  }, [storageKey])

  useEffect(() => {
    if (!identityReady) {
      setState(createInitialWorkbenchState())
      setHydratedKey(null)
      return
    }
    try {
      const restored = parseWorkbenchState(window.localStorage.getItem(storageKey))
      setState(restored ?? createInitialWorkbenchState())
    } catch {
      setState(createInitialWorkbenchState())
      toast.error('本机会话暂时无法读取', {
        description: '浏览器存储不可用；已使用空白本地投影，不会重放 Provider 请求。',
      })
    }
    setHydratedKey(storageKey)
  }, [identityReady, storageKey])

  useEffect(() => {
    if (identityReady && hydratedKey === storageKey) {
      const prepared = prepareWorkbenchStateForPersistence(state)
      if (!prepared.ok) {
        const warningKey = `${storageKey}:${prepared.code}`
        if (persistenceWarningKeyRef.current !== warningKey) {
          persistenceWarningKeyRef.current = warningKey
          toast.error('本机会话未保存', {
            description:
              prepared.code === 'protected_capacity_exceeded'
                ? '固定会话与当前会话已达到本地容量上限；请取消固定或归档不再需要的会话。'
                : '会话状态未通过完整性校验；为避免写入损坏记录，本次保存已拒绝。',
          })
        }
        return
      }
      if (prepared.evictedSessionIds.length > 0) {
        setState(prepared.state)
        toast.info('已整理本机会话', {
          description: `为保持 4 MiB 本地上限，已移除 ${prepared.evictedSessionIds.length} 个最旧且未固定的非当前会话。`,
        })
        return
      }
      try {
        window.localStorage.setItem(storageKey, prepared.serialized)
        persistenceWarningKeyRef.current = null
      } catch {
        if (persistenceWarningKeyRef.current !== storageKey) {
          persistenceWarningKeyRef.current = storageKey
          toast.error('本机会话暂时无法保存', {
            description: '浏览器存储空间不足或不可用；当前页面仍可使用，但刷新前请勿依赖本地历史。',
          })
        }
      }
    }
  }, [hydratedKey, identityReady, state, storageKey])

  useEffect(() => {
    if (hydratedKey !== storageKey || !workspaces) return
    const restoredWorkspaceId = activeSession?.workspaceId ?? ''
    const authorized = workspaces.items.some((candidate) => candidate.id === restoredWorkspaceId)
    setWorkspaceId(authorized ? restoredWorkspaceId : '')
  }, [activeSession?.id, activeSession?.workspaceId, hydratedKey, storageKey, workspaces])

  useEffect(() => {
    const generation = runtimeRequestGenerationRef.current + 1
    runtimeRequestGenerationRef.current = generation
    setProfiles([])
    setAgentVersionId('')
    setPosture(null)
    setRuntimeError(null)
    const workspaceAuthorized = workspaces?.items.some((candidate) => candidate.id === workspaceId)
    if (!identityReady || hydratedKey !== storageKey || !workspaceId || !workspaceAuthorized) return
    Promise.all([agentAlphaApi.profiles(workspaceId), agentAlphaApi.status(workspaceId)])
      .then(([profileList, status]) => {
        if (runtimeRequestGenerationRef.current !== generation) return
        setProfiles(profileList.items)
        setAgentVersionId(profileList.items[0]?.agent_version_id ?? '')
        setPosture(status)
      })
      .catch((error: unknown) => {
        if (runtimeRequestGenerationRef.current !== generation) return
        setRuntimeError(getApiErrorMessage(error, '运行时状态不可用'))
      })
  }, [hydratedKey, identityReady, storageKey, workspaceId, workspaces])

  useEffect(
    () => () => {
      guardRef.current.stop()
    },
    [],
  )

  const sessions = useMemo(
    () => listWorkbenchSessions(state, { query, archived: archiveMode }),
    [archiveMode, query, state],
  )
  const workspace = workspaces?.items.find((candidate) => candidate.id === workspaceId)
  const activeEmployee = employeeById(activeEmployeeId)
  useEffect(() => {
    setTaskBinding(null)
  }, [activeSession?.id])

  const mutate = (updater: (current: WorkbenchState) => WorkbenchState) => setState(updater)
  const publishModelSettings = (
    requestedScope: ModelSettingsScope,
    items: AgentModelSettingRead[],
  ) => {
    if (
      modelSettingsScopeRef.current.key === requestedScope.key &&
      modelSettingsScopeRef.current.generation === requestedScope.generation
    ) {
      setModelSettingsProjection({ scope: requestedScope, items })
    }
  }

  async function submit(): Promise<void> {
    if (submitInFlightRef.current) {
      toast.info('任务已在准备中', {
        description: '请等待当前准备或调用完成，本次重复提交未发出。',
      })
      return
    }
    if (!activeSession || phase !== 'idle' || fileMutating) return
    const readyProjection = projectionForScope(
      modelSettingsProjection,
      modelSettingsScopeRef.current,
    )
    if (!readyProjection) return
    submitInFlightRef.current = true
    setPreparing(true)
    const preparationScope = {
      storageKey,
      sessionId: activeSession.id,
      workspaceId: activeSession.workspaceId ?? '',
      agentVersionId,
      gear,
    }
    const route = parseEmployeeInvocation(input)
    if (!route.ok) {
      toast.error('无法发送任务', { description: route.message })
      submitInFlightRef.current = false
      setPreparing(false)
      return
    }
    const modelSettingPreparation = captureModelSettingPreparation(
      readyProjection,
      route.employee.id,
    )
    if (!modelSettingPreparation) {
      submitInFlightRef.current = false
      setPreparing(false)
      return
    }
    const preparedRoleMessage = prepareEmployeeRoleMessage(route.employee, route.message)
    if (!preparedRoleMessage.ok) {
      toast.error('任务内容过长', {
        description: `加入员工职责边界后共有 ${preparedRoleMessage.actualCharacters.toLocaleString()} 个字符，超过 ${preparedRoleMessage.maximumCharacters.toLocaleString()} 字符上限；任务未发出。`,
      })
      submitInFlightRef.current = false
      setPreparing(false)
      return
    }
    const selectedModelSetting = readyProjection.items.find(
      (item) => item.employee_role_id === route.employee.id,
    )
    if (!selectedModelSetting || selectedModelSetting.state === 'unavailable') {
      toast.error('当前员工没有可用模型', {
        description: '请先配置并验证个人 Provider 或该员工的专属模型。',
      })
      submitInFlightRef.current = false
      setPreparing(false)
      return
    }
    if (selectedModelSetting.state === 'pending') {
      toast.error('当前员工模型尚未验证', {
        description: '自定义模型名称必须先通过精确身份验证，任务未发出。',
      })
      submitInFlightRef.current = false
      setPreparing(false)
      return
    }
    const selectedGear = P6_GEAR_PROFILES[preparationScope.gear]
    const adaptation = buildP6AdaptationInstruction(
      {
        providerId: selectedModelSetting.effective_provider_id,
        modelId: selectedModelSetting.effective_model_id,
        familyOverride:
          selectedModelSetting.family_source === 'explicit_override'
            ? selectedModelSetting.family
            : null,
      },
      preparationScope.gear,
    )
    const adaptedRoleMessage = `${preparedRoleMessage.roleMessage}\n\n${adaptation}`
    let fileCompilation
    try {
      fileCompilation = await filePanelRef.current?.compileContext(
        adaptedRoleMessage,
        selectedGear.contextCharacterBudget,
      )
    } catch (error) {
      submitInFlightRef.current = false
      setPreparing(false)
      toast.error('文件上下文读取失败', {
        description: getApiErrorMessage(error, 'p6_file_context_failed'),
      })
      return
    }
    if (fileCompilation && !fileCompilation.ok) {
      toast.error('文件上下文未通过校验', {
        description: `${fileCompilation.code}${fileCompilation.entryId ? ` · ${fileCompilation.entryId}` : ''}；任务未发出。`,
      })
      submitInFlightRef.current = false
      setPreparing(false)
      return
    }
    const finalMessage = `${adaptedRoleMessage}${fileCompilation?.ok ? fileCompilation.context.promptFragment : ''}`
    if (finalMessage.length > P6_AGENT_ALPHA_MAX_MESSAGE_CHARACTERS) {
      toast.error('最终请求超过安全上限', {
        description: `员工职责、模型适配与文件上下文合计 ${finalMessage.length.toLocaleString()} 个字符；任务未发出。`,
      })
      submitInFlightRef.current = false
      setPreparing(false)
      return
    }
    const currentScope = preparationScopeRef.current
    if (
      currentScope.storageKey !== preparationScope.storageKey ||
      currentScope.sessionId !== preparationScope.sessionId ||
      currentScope.workspaceId !== preparationScope.workspaceId ||
      currentScope.agentVersionId !== preparationScope.agentVersionId ||
      currentScope.gear !== preparationScope.gear ||
      !modelSettingPreparationIsCurrent(
        modelSettingPreparation,
        modelSettingsProjection,
        modelSettingsScopeRef.current,
      )
    ) {
      submitInFlightRef.current = false
      setPreparing(false)
      toast.error('任务准备期间上下文发生变化', {
        description: '会话、Workspace、Agent 或挡位已变化；旧文件上下文未发送，请重新提交。',
      })
      return
    }
    const invocationWorkspaceId = preparationScope.workspaceId
    if (workspaceId !== invocationWorkspaceId) {
      toast.error('会话与 Workspace 不一致', {
        description: '请重新选择当前会话的 Workspace 后再发送；任务未发出。',
      })
      submitInFlightRef.current = false
      setPreparing(false)
      return
    }
    if (!canInvokeAgent(posture, route.message, invocationWorkspaceId, agentVersionId)) {
      toast.error('Agent Runtime 尚未就绪', {
        description: runtimeError ?? '请选择 Workspace 与已安装 Agent，并确认个人运行时可用。',
      })
      submitInFlightRef.current = false
      setPreparing(false)
      return
    }

    const guard = guardRef.current
    const started = guard.begin()
    if (!started) {
      submitInFlightRef.current = false
      setPreparing(false)
      return
    }
    const { generation, controller } = started
    setPreparing(false)
    const sessionId = preparationScope.sessionId
    const employee = route.employee
    const invocationAgentVersionId = preparationScope.agentVersionId
    invocationContextRef.current = {
      generation,
      sessionId,
      workspaceId: invocationWorkspaceId,
      agentVersionId: invocationAgentVersionId,
      invocationId: null,
      taskId: null,
      cancellationIdentityUnavailable: false,
    }
    const visibleMessage = route.explicitMention
      ? `@${employee.displayName} ${route.message}`
      : route.message
    const visiblePersistence = sanitizeWorkbenchPersistenceText(visibleMessage)
    if (visiblePersistence.redacted) {
      toast.warning('敏感内容未写入本机会话', {
        description: '检测到密钥、令牌、数据库地址或物理路径；本地历史仅保存安全占位符。',
      })
    } else if (visiblePersistence.truncated) {
      toast.info('超长消息已截断保存', {
        description: 'Provider 请求仍使用通过安全预检的完整内容；本地会话只保留有界副本。',
      })
    }
    mutate((current) =>
      appendWorkbenchMessage(current, sessionId, {
        role: 'user',
        employeeId: employee.id,
        content: visibleMessage,
      }),
    )
    mutate((current) =>
      appendWorkbenchTimelineEvent(current, sessionId, {
        kind: 'invocation_started',
        label: `${employee.displayName} 调用已开始`,
        employeeId: employee.id,
      }),
    )
    if (activeSession.messages.length === 0 && activeSession.title === 'P6.0 工作台') {
      mutate((current) => renameSession(current, sessionId, route.message.slice(0, 36)))
    }
    setActiveEmployeeId(employee.id)
    setInput('')
    setStreaming('')
    setUsage(null)
    setTaskBinding(null)

    setPhase('running')
    try {
      const response = await agentAlphaApi.invokeStream(
        invocationWorkspaceId,
        {
          agent_version_id: invocationAgentVersionId,
          employee_role_id: employee.id,
          message: finalMessage,
          top_k: selectedGear.topK,
        },
        { signal: controller.signal },
      )
      if (!response.ok || !response.body) throw new Error(`agent_alpha_http_${response.status}`)
      const terminal = await consumeAgentAlphaStream(response.body.getReader(), {
        onMeta: (meta) => {
          if (!guard.isCurrent(generation)) return
          if (invocationContextRef.current?.generation === generation) {
            invocationContextRef.current.invocationId = meta.invocationId
            invocationContextRef.current.taskId = meta.taskId
          }
          if (meta.identity) setIdentity(meta.identity)
          setModelIdentity({
            providerId: meta.providerId,
            modelId: meta.actualModelId ?? meta.requestedModelId,
          })
        },
        onChunk: (chunk) => {
          if (guard.isCurrent(generation)) setStreaming((current) => current + chunk)
        },
        onUsage: (nextUsage) => {
          if (guard.isCurrent(generation)) setUsage(nextUsage)
        },
      })
      if (!guard.isCurrent(generation)) return
      if (
        terminal.kind === 'done' &&
        terminal.taskId &&
        terminal.invocationId &&
        invocationContextRef.current?.taskId === terminal.taskId &&
        invocationContextRef.current.invocationId === terminal.invocationId
      ) {
        setTaskBinding({ taskId: terminal.taskId, invocationId: terminal.invocationId })
      }
      const cancellationIdentityUnavailable =
        terminal.kind === 'cancelled' &&
        invocationContextRef.current?.generation === generation &&
        invocationContextRef.current.cancellationIdentityUnavailable
      const content =
        terminal.kind === 'done'
          ? terminal.answer
          : terminal.kind === 'cancelled'
            ? cancellationIdentityUnavailable
              ? '本地连接已中断；服务器最终状态未知。'
              : '任务已取消。'
            : `任务失败：${terminal.code}`
      const contentPersistence = sanitizeWorkbenchPersistenceText(content)
      if (contentPersistence.redacted) {
        toast.warning('Agent 返回的敏感内容未写入本机会话', {
          description: '本地历史仅保存安全占位符；流式临时内容不会作为持久化记录保留。',
        })
      } else if (contentPersistence.truncated) {
        toast.info('Agent 超长回复已截断保存', {
          description: '本次流式回复已完整展示；本地会话保留带截断标记的有界副本。',
        })
      }
      mutate((current) =>
        appendWorkbenchMessage(current, sessionId, {
          role: 'agent',
          employeeId: employee.id,
          content,
        }),
      )
      mutate((current) =>
        appendWorkbenchTimelineEvent(current, sessionId, {
          kind:
            terminal.kind === 'done'
              ? 'invocation_completed'
              : terminal.kind === 'cancelled'
                ? cancellationIdentityUnavailable
                  ? 'invocation_interrupted_unknown'
                  : 'invocation_cancelled'
                : 'invocation_failed',
          label:
            terminal.kind === 'done'
              ? `${employee.displayName} 调用已完成`
              : terminal.kind === 'cancelled'
                ? cancellationIdentityUnavailable
                  ? `${employee.displayName} 本地连接已中断，服务器状态未知`
                  : `${employee.displayName} 调用已取消`
                : `${employee.displayName} 调用失败：${terminal.code}`,
          employeeId: employee.id,
        }),
      )
      setStreaming('')
    } catch (error: unknown) {
      if (!guard.isCurrent(generation)) return
      const userCancelled = isUserCancelledError(error)
      const cancellationIdentityUnavailable =
        userCancelled &&
        invocationContextRef.current?.generation === generation &&
        invocationContextRef.current.cancellationIdentityUnavailable
      const content = userCancelled
        ? cancellationIdentityUnavailable
          ? '本地连接已中断；服务器最终状态未知。'
          : '任务已取消。'
        : error instanceof Error && error.message === 'auth_session_expired'
          ? '登录状态已失效，请重新登录后再试。'
          : `任务失败：${getApiErrorMessage(error, 'agent_alpha_failed')}`
      mutate((current) =>
        appendWorkbenchMessage(current, sessionId, {
          role: 'agent',
          employeeId: employee.id,
          content,
        }),
      )
      mutate((current) =>
        appendWorkbenchTimelineEvent(current, sessionId, {
          kind: userCancelled
            ? cancellationIdentityUnavailable
              ? 'invocation_interrupted_unknown'
              : 'invocation_cancelled'
            : 'invocation_failed',
          label: userCancelled
            ? cancellationIdentityUnavailable
              ? `${employee.displayName} 本地连接已中断，服务器状态未知`
              : `${employee.displayName} 调用已取消`
            : `${employee.displayName} 调用失败`,
          employeeId: employee.id,
        }),
      )
      setStreaming('')
    } finally {
      if (employee.id !== 'parent') {
        mutate((current) =>
          appendWorkbenchTimelineEvent(current, sessionId, {
            kind: 'employee_returned_dormant',
            label: `${employee.displayName} 已恢复静默`,
            employeeId: employee.id,
          }),
        )
      }
      guard.settle(generation, controller)
      if (invocationContextRef.current?.generation === generation) {
        invocationContextRef.current = null
      }
      setPhase(guard.phase)
      setActiveEmployeeId('parent')
      submitInFlightRef.current = false
    }
  }

  async function stop(): Promise<void> {
    const controller = guardRef.current.stop()
    if (!controller) return
    setPhase('cancelling')
    const invocation = invocationContextRef.current
    if (invocation?.workspaceId && invocation.invocationId) {
      await agentAlphaApi
        .cancel(invocation.workspaceId, invocation.invocationId)
        .catch(() => undefined)
    } else {
      if (invocation?.generation !== undefined) {
        invocation.cancellationIdentityUnavailable = true
      }
      toast.info('已中断本地连接', {
        description: '服务器尚未返回调用标识，无法发送精确取消；最终状态将由服务器账本收敛。',
      })
    }
  }

  if (!activeSession) return null

  return (
    <div className="fade-up flex h-[calc(100vh-6rem)] min-h-[42rem] flex-col overflow-hidden rounded-2xl border border-border bg-background shadow-[0_30px_100px_-60px_rgba(0,0,0,.65)]">
      <TopBar
        workspaceId={workspaceId}
        workspaces={workspaces?.items ?? []}
        workspaceError={Boolean(workspaceError)}
        onWorkspaceChange={(value) => {
          if (phase !== 'idle' || preparing || fileMutating) return
          setTaskBinding(null)
          setWorkspaceId(value)
          mutate((current) => ({
            ...current,
            sessions: current.sessions.map((session) =>
              session.id === current.activeSessionId ? { ...session, workspaceId: value } : session,
            ),
          }))
        }}
        profiles={profiles}
        agentVersionId={agentVersionId}
        onAgentChange={(value) => {
          if (phase === 'idle' && !preparing && !fileMutating) setAgentVersionId(value)
        }}
        posture={posture}
        tokens={estimateSessionTokens(activeSession)}
        locked={phase !== 'idle' || preparing || fileMutating}
        gear={gear}
        onGearChange={setGear}
      />
      <div className="grid min-h-0 flex-1 grid-cols-1 lg:grid-cols-[16.5rem_minmax(0,1fr)_19rem]">
        <SessionRail
          state={state}
          sessions={sessions}
          query={query}
          archiveMode={archiveMode}
          onQuery={setQuery}
          onArchiveMode={setArchiveMode}
          locked={phase !== 'idle' || preparing || fileMutating}
          onCreate={() => {
            if (phase !== 'idle' || preparing || fileMutating) return
            const result = tryAddSession(state, '新会话', undefined, workspaceId || null)
            if (!result.ok) {
              toast.error('无法新建会话', {
                description: '80 个会话均为固定或当前会话；请先取消固定或归档一个会话。',
              })
              return
            }
            setState(result.state)
          }}
          onSelect={(id) => {
            if (phase !== 'idle' || preparing || fileMutating) return
            setTaskBinding(null)
            const target = state.sessions.find((session) => session.id === id)
            setWorkspaceId(target?.workspaceId ?? '')
            mutate((current) => setActiveSession(current, id))
          }}
          onPin={(session) => {
            if (phase === 'idle' && !preparing && !fileMutating) {
              mutate((current) => setSessionPinned(current, session.id, !session.pinned))
            }
          }}
          onArchive={(session) => {
            if (phase === 'idle' && !preparing && !fileMutating) {
              mutate((current) =>
                setSessionArchived(current, session.id, session.archivedAt === null),
              )
            }
          }}
        />
        <ConversationPane
          session={activeSession}
          employee={activeEmployee}
          input={input}
          streaming={streaming}
          phase={preparing ? 'preparing' : phase}
          interactionLocked={fileMutating}
          ready={
            modelSettingsReady &&
            !fileMutating &&
            canInvokeAgent(posture, input, workspaceId, agentVersionId)
          }
          maximumCharacters={P6_AGENT_ALPHA_MAX_MESSAGE_CHARACTERS}
          onInput={setInput}
          onSubmit={() => void submit()}
          onStop={() => void stop()}
          onMention={(employee) => {
            if (fileMutating) return
            setInput((current) => `@${employee.shortName} ${current.replace(/^@\S+\s*/, '')}`)
          }}
        />
        <ContextRail
          workspace={workspace}
          documents={documents?.items ?? []}
          employee={activeEmployee}
          identity={identity}
          modelIdentity={modelIdentity}
          gear={gear}
          usage={usage}
          timelineOpen={timelineOpen}
          session={activeSession}
          modelSettings={modelSettings}
          onTimeline={() => setTimelineOpen((current) => !current)}
          onConfigureModel={(selectedEmployee) => {
            if (!modelSettingsReady) return
            const setting = modelSettings.find(
              (item) => item.employee_role_id === selectedEmployee.id,
            )
            setModelEditorRole(selectedEmployee.id)
            setModelEditorCredential(setting?.override_credential_id ?? '')
            setModelEditorName(setting?.requested_model_id ?? '')
          }}
          filePanel={
            <WorkspaceFilePanel
              ref={filePanelRef}
              tenantId={tenant?.id ?? ''}
              workspaceId={workspaceId}
              sessionId={activeSession.id}
              taskBinding={taskBinding}
              locked={phase !== 'idle' || preparing || fileMutating}
              onMutationChange={setFileMutating}
            />
          }
        />
      </div>
      <Dialog
        open={modelEditorRole !== null}
        onOpenChange={(open) => !open && setModelEditorRole(null)}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>配置专属模型</DialogTitle>
            <DialogDescription>
              默认继承当前个人 Provider。独立配置只引用已加密保存的凭据，不复制或回显 API Key。
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4">
            <label className="space-y-1 text-sm">
              <span className="text-xs text-muted-foreground">已保存的 Provider 连接</span>
              <select
                value={modelEditorCredential}
                onChange={(event) => setModelEditorCredential(event.target.value)}
                className="h-10 w-full rounded-md border bg-background px-3 text-sm"
              >
                <option value="">继承默认 URL 与 Key</option>
                {credentials.map((credential) => (
                  <option key={credential.id} value={credential.id}>
                    {credential.display_name} · {credential.model_id}
                  </option>
                ))}
              </select>
            </label>
            <label className="space-y-1 text-sm">
              <span className="text-xs text-muted-foreground">模型名称（名称优先识别）</span>
              <Input
                value={modelEditorName}
                onChange={(event) => setModelEditorName(event.target.value)}
                placeholder="例如 deepseek-v4-pro / kimi-k3 / claude-sonnet-5"
                maxLength={200}
              />
            </label>
          </div>
          <DialogFooter>
            {modelSettings.find((item) => item.employee_role_id === modelEditorRole)
              ?.requested_model_id ? (
              <Button
                variant="secondary"
                disabled={!modelSettingsReady || savingModelSetting || testingModelSetting}
                onClick={() => {
                  if (!modelEditorRole) return
                  const requestedScope = modelSettingsScopeRef.current
                  setTestingModelSetting(true)
                  void agentAlphaApi
                    .testModelSetting(workspaceId, agentVersionId, modelEditorRole)
                    .then((result) => {
                      toast[result.status === 'passed' ? 'success' : 'error'](
                        result.status === 'passed' ? '专属模型验证通过' : '专属模型验证失败',
                        { description: `状态：${result.status} · ${result.latency_ms} ms` },
                      )
                      return agentAlphaApi.modelSettings(workspaceId, agentVersionId)
                    })
                    .then((settings) => publishModelSettings(requestedScope, settings.items))
                    .catch((reason) =>
                      toast.error('专属模型验证失败', {
                        description: getApiErrorMessage(reason, 'p6_model_setting_test_failed'),
                      }),
                    )
                    .finally(() => setTestingModelSetting(false))
                }}
              >
                验证模型
              </Button>
            ) : null}
            <Button
              variant="outline"
              disabled={!modelSettingsReady || savingModelSetting || testingModelSetting}
              onClick={() => {
                if (!modelEditorRole) return
                const requestedScope = modelSettingsScopeRef.current
                setSavingModelSetting(true)
                void agentAlphaApi
                  .deleteModelSetting(
                    workspaceId,
                    agentVersionId,
                    modelEditorRole,
                    modelSettings.find((item) => item.employee_role_id === modelEditorRole)
                      ?.version ?? 0,
                  )
                  .then((updated) => {
                    publishModelSettings(
                      requestedScope,
                      modelSettings.map((item) =>
                        item.employee_role_id === updated.employee_role_id ? updated : item,
                      ),
                    )
                    setModelEditorRole(null)
                    toast.success('已恢复继承默认模型')
                  })
                  .catch((reason) =>
                    toast.error('恢复默认模型失败', {
                      description: getApiErrorMessage(reason, 'p6_model_setting_delete_failed'),
                    }),
                  )
                  .finally(() => setSavingModelSetting(false))
              }}
            >
              恢复默认
            </Button>
            <Button
              disabled={
                !modelSettingsReady ||
                savingModelSetting ||
                testingModelSetting ||
                (!modelEditorCredential && !modelEditorName.trim())
              }
              onClick={() => {
                if (!modelEditorRole) return
                const requestedScope = modelSettingsScopeRef.current
                const current = modelSettings.find(
                  (item) => item.employee_role_id === modelEditorRole,
                )
                setSavingModelSetting(true)
                void agentAlphaApi
                  .updateModelSetting(workspaceId, agentVersionId, modelEditorRole, {
                    inherit_default: false,
                    provider_credential_id: modelEditorCredential || null,
                    requested_model_id: modelEditorName.trim() || null,
                    expected_version: current?.version ?? 0,
                  })
                  .then((updated) => {
                    publishModelSettings(
                      requestedScope,
                      modelSettings.map((item) =>
                        item.employee_role_id === updated.employee_role_id ? updated : item,
                      ),
                    )
                    setModelEditorRole(null)
                    toast.success('专属模型配置已保存')
                  })
                  .catch((reason) =>
                    toast.error('专属模型配置失败', {
                      description: getApiErrorMessage(reason, 'p6_model_setting_update_failed'),
                    }),
                  )
                  .finally(() => setSavingModelSetting(false))
              }}
            >
              保存配置
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}

function TopBar({
  workspaceId,
  workspaces,
  workspaceError,
  onWorkspaceChange,
  profiles,
  agentVersionId,
  onAgentChange,
  posture,
  tokens,
  locked,
  gear,
  onGearChange,
}: {
  workspaceId: string
  workspaces: WorkspaceRead[]
  workspaceError: boolean
  onWorkspaceChange: (value: string) => void
  profiles: AgentAlphaProfile[]
  agentVersionId: string
  onAgentChange: (value: string) => void
  posture: RuntimePosture | null
  tokens: number
  locked: boolean
  gear: P6ReasoningGear
  onGearChange: (value: P6ReasoningGear) => void
}) {
  return (
    <header className="flex h-14 shrink-0 items-center gap-2 border-b border-border px-3">
      <span className="flex items-center gap-2 px-2 text-xs font-semibold">
        <BrainCircuit className="h-4 w-4" /> Personal Engineering Workbench
      </span>
      <select
        value={workspaceId}
        onChange={(event) => onWorkspaceChange(event.target.value)}
        disabled={locked}
        className="h-8 max-w-48 rounded-md border border-border bg-card px-2 text-[10px] outline-none"
        aria-label="选择 Workspace"
      >
        <option value="">{workspaceError ? 'Workspace 不可用' : '选择 Workspace'}</option>
        {workspaces.map((workspace) => (
          <option key={workspace.id} value={workspace.id}>
            {workspace.display_name}
          </option>
        ))}
      </select>
      <select
        value={agentVersionId}
        onChange={(event) => onAgentChange(event.target.value)}
        disabled={locked}
        className="hidden h-8 max-w-44 rounded-md border border-border bg-card px-2 text-[8px] outline-none lg:block"
        aria-label="选择 Agent"
      >
        <option value="">未安装 Agent</option>
        {profiles.map((profile) => (
          <option key={profile.agent_version_id} value={profile.agent_version_id}>
            {profile.display_name}
          </option>
        ))}
      </select>
      <select
        value={gear}
        onChange={(event) => onGearChange(event.target.value as P6ReasoningGear)}
        disabled={locked}
        className="h-8 rounded-md border border-border bg-card px-2 text-[9px] outline-none"
        aria-label="选择推理挡位"
      >
        {(Object.values(P6_GEAR_PROFILES) as Array<(typeof P6_GEAR_PROFILES)[P6ReasoningGear]>).map(
          (profile) => (
            <option key={profile.id} value={profile.id}>
              {profile.displayName}
            </option>
          ),
        )}
      </select>
      <div className="ml-auto hidden gap-2 md:flex">
        <Status label={`Context ~${tokens} tok`} />
        <Status
          label={agentInvokeConditionsMet(posture) ? 'Runtime ready' : 'Runtime locked'}
          muted={!agentInvokeConditionsMet(posture)}
        />
      </div>
    </header>
  )
}

function Status({ label, muted = false }: { label: string; muted?: boolean }) {
  return (
    <span
      className={cn(
        'hidden h-8 items-center rounded-md border border-border px-2 font-mono text-[8px] lg:flex',
        muted && 'text-muted-foreground',
      )}
    >
      {label}
    </span>
  )
}

function SessionRail({
  state,
  sessions,
  query,
  archiveMode,
  onQuery,
  onArchiveMode,
  onCreate,
  onSelect,
  onPin,
  onArchive,
  locked,
}: {
  state: WorkbenchState
  sessions: readonly WorkbenchSession[]
  query: string
  archiveMode: boolean
  onQuery: (value: string) => void
  onArchiveMode: (value: boolean) => void
  onCreate: () => void
  onSelect: (id: string) => void
  onPin: (session: WorkbenchSession) => void
  onArchive: (session: WorkbenchSession) => void
  locked: boolean
}) {
  return (
    <aside className="hidden min-h-0 flex-col border-r border-border bg-muted/15 lg:flex">
      <div className="space-y-2 border-b border-border p-3">
        <Button className="h-9 w-full justify-start text-xs" onClick={onCreate} disabled={locked}>
          <MessageSquarePlus className="h-3.5 w-3.5" />
          新建会话
        </Button>
        <div className="relative">
          <Search className="absolute left-2.5 top-2.5 h-3.5 w-3.5 text-muted-foreground" />
          <Input
            value={query}
            onChange={(event) => onQuery(event.target.value)}
            placeholder="搜索会话与消息"
            className="h-9 pl-8 text-[10px]"
          />
        </div>
        <div className="grid grid-cols-2 gap-1 rounded-lg bg-muted p-1">
          <button
            type="button"
            onClick={() => onArchiveMode(false)}
            className={cn(
              'rounded-md py-1.5 text-[9px]',
              !archiveMode && 'bg-background shadow-sm',
            )}
          >
            会话
          </button>
          <button
            type="button"
            onClick={() => onArchiveMode(true)}
            className={cn('rounded-md py-1.5 text-[9px]', archiveMode && 'bg-background shadow-sm')}
          >
            已归档
          </button>
        </div>
      </div>
      <div className="min-h-0 flex-1 space-y-1 overflow-y-auto p-2">
        {sessions.map((session) => (
          <div
            key={session.id}
            className={cn(
              'group rounded-xl border px-2.5 py-2.5',
              session.id === state.activeSessionId
                ? 'border-foreground/25 bg-background'
                : 'border-transparent hover:border-border hover:bg-background/65',
            )}
          >
            <button
              type="button"
              className="w-full text-left"
              onClick={() => onSelect(session.id)}
              disabled={locked}
            >
              <div className="flex items-center gap-2">
                {session.pinned ? (
                  <Pin className="h-3 w-3" />
                ) : (
                  <MessageSquarePlus className="h-3 w-3" />
                )}
                <span className="min-w-0 flex-1 truncate text-[11px] font-medium">
                  {session.title}
                </span>
                <span className="font-mono text-[7px] text-muted-foreground">
                  {shortTime(session.updatedAt)}
                </span>
              </div>
              <p className="mt-1.5 truncate text-[8px] text-muted-foreground">
                {session.messages.at(-1)?.content ?? '尚无消息'}
              </p>
            </button>
            <div className="mt-2 hidden items-center gap-1 group-hover:flex">
              <button
                type="button"
                onClick={() => onPin(session)}
                disabled={locked}
                className="rounded p-1 text-muted-foreground hover:bg-muted"
              >
                <Pin className="h-3 w-3" />
              </button>
              <button
                type="button"
                onClick={() => onArchive(session)}
                disabled={locked}
                className="rounded p-1 text-muted-foreground hover:bg-muted"
              >
                {session.archivedAt ? (
                  <RotateCcw className="h-3 w-3" />
                ) : (
                  <Archive className="h-3 w-3" />
                )}
              </button>
            </div>
          </div>
        ))}
        {sessions.length === 0 && (
          <p className="px-3 py-8 text-center text-[9px] text-muted-foreground">没有匹配会话</p>
        )}
      </div>
    </aside>
  )
}

function ConversationPane({
  session,
  employee,
  input,
  streaming,
  phase,
  interactionLocked,
  ready,
  maximumCharacters,
  onInput,
  onSubmit,
  onStop,
  onMention,
}: {
  session: WorkbenchSession
  employee: EmployeeDefinition
  input: string
  streaming: string
  phase: WorkbenchInvocationPhase
  interactionLocked: boolean
  ready: boolean
  maximumCharacters: number
  onInput: (value: string) => void
  onSubmit: () => void
  onStop: () => void
  onMention: (employee: EmployeeDefinition) => void
}) {
  return (
    <main className="flex min-h-0 min-w-0 flex-col">
      <header className="flex h-12 shrink-0 items-center border-b border-border px-4">
        <div>
          <h1 className="text-xs font-semibold">{session.title}</h1>
          <p className="font-mono text-[7px] uppercase text-muted-foreground">
            Owner session · local durable draft · one employee per message
          </p>
        </div>
      </header>
      <div className="min-h-0 flex-1 overflow-y-auto px-4 py-6 sm:px-8">
        {session.messages.length === 0 && !streaming ? (
          <div className="mx-auto flex h-full max-w-3xl flex-col justify-center">
            <div className="flex items-center gap-3">
              <span className="flex h-12 w-12 items-center justify-center rounded-2xl border bg-muted">
                <Sparkles className="h-5 w-5" />
              </span>
              <div>
                <p className="font-mono text-[8px] uppercase tracking-[0.22em] text-muted-foreground">
                  P6.0-A / Parent Agent active
                </p>
                <h2 className="mt-1 text-2xl font-semibold">从一个真实任务开始</h2>
              </div>
            </div>
            <p className="mt-4 max-w-2xl text-sm leading-6 text-muted-foreground">
              不使用 @ 时由父 Agent
              负责；每条消息最多唤醒一名静默员工。员工完成后恢复静默，不会相互唤醒或后台运行。
            </p>
            <div className="mt-6 grid gap-2 sm:grid-cols-3">
              {PERSONAL_EMPLOYEES.filter((item) => item.id !== 'parent')
                .slice(0, 6)
                .map((item) => (
                  <button
                    key={item.id}
                    type="button"
                    onClick={() => onMention(item)}
                    className="rounded-xl border bg-muted/20 p-3 text-left hover:bg-muted"
                  >
                    <span className="text-[10px] font-semibold">@{item.displayName}</span>
                    <span className="mt-1 block text-[8px] leading-4 text-muted-foreground">
                      {item.responsibility}
                    </span>
                  </button>
                ))}
            </div>
          </div>
        ) : (
          <div className="mx-auto max-w-4xl space-y-5">
            {session.messages.map((message) => {
              const actor = employeeById(message.employeeId)
              const isUser = message.role === 'user'
              return (
                <article
                  key={message.id}
                  className={cn('flex gap-3', isUser && 'flex-row-reverse')}
                >
                  <span
                    className={cn(
                      'flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border',
                      isUser ? 'bg-foreground text-background' : 'bg-muted',
                    )}
                  >
                    {isUser ? <User className="h-3.5 w-3.5" /> : <Bot className="h-3.5 w-3.5" />}
                  </span>
                  <div className="max-w-[84%]">
                    <div className="mb-1 text-[8px] text-muted-foreground">
                      {isUser ? '你' : actor.displayName} · {shortTime(message.createdAt)}
                    </div>
                    <div
                      className={cn(
                        'whitespace-pre-wrap rounded-2xl px-4 py-3 text-sm leading-6',
                        isUser
                          ? 'rounded-tr-sm bg-foreground text-background'
                          : 'rounded-tl-sm border bg-muted/45',
                      )}
                    >
                      {message.content}
                    </div>
                  </div>
                </article>
              )
            })}
            {streaming && (
              <article className="flex gap-3">
                <span className="flex h-8 w-8 items-center justify-center rounded-lg border bg-muted">
                  <Bot className="h-3.5 w-3.5" />
                </span>
                <div className="max-w-[84%]">
                  <div className="mb-1 text-[8px] text-muted-foreground">
                    {employee.displayName} · 工作中
                  </div>
                  <div className="whitespace-pre-wrap rounded-2xl rounded-tl-sm border bg-muted/45 px-4 py-3 text-sm leading-6">
                    {streaming}
                    <span className="ml-1 inline-block h-4 w-0.5 animate-pulse bg-foreground" />
                  </div>
                </div>
              </article>
            )}
          </div>
        )}
      </div>
      <footer className="shrink-0 border-t p-3 sm:p-4">
        <div className="mx-auto max-w-4xl rounded-2xl border bg-card p-2.5 shadow-lg">
          <textarea
            value={input}
            onChange={(event) => onInput(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter' && !event.shiftKey) {
                event.preventDefault()
                if (phase === 'idle' && !interactionLocked) onSubmit()
              }
            }}
            rows={3}
            maxLength={maximumCharacters}
            disabled={phase !== 'idle' || interactionLocked}
            placeholder="交给父 Agent，或输入 @前端工程师 / @安全架构师 唤醒一名员工…"
            className="w-full resize-none bg-transparent px-2 py-1.5 text-sm leading-6 outline-none placeholder:text-muted-foreground disabled:opacity-60"
          />
          <div className="mt-2 flex items-center justify-between gap-3 border-t pt-2">
            <div className="flex min-w-0 items-center gap-2">
              <Badge variant="outline" className="text-[8px]">
                {employee.id === 'parent'
                  ? '父 Agent · Active'
                  : `${employee.displayName} · Invoked`}
              </Badge>
              <span className="hidden truncate text-[8px] text-muted-foreground sm:block">
                其他 9 名员工静默 · 禁止自动委派
              </span>
              <span className="font-mono text-[8px] text-muted-foreground">
                {input.length.toLocaleString()} / {maximumCharacters.toLocaleString()}
              </span>
            </div>
            {phase === 'idle' ? (
              <Button
                size="sm"
                onClick={onSubmit}
                disabled={interactionLocked || !input.trim() || !ready}
              >
                发送
                <Send className="h-3.5 w-3.5" />
              </Button>
            ) : phase === 'preparing' ? (
              <Button size="sm" disabled>
                准备上下文
                <CircleDot className="h-3.5 w-3.5 animate-pulse" />
              </Button>
            ) : (
              <Button size="sm" variant="destructive" onClick={onStop}>
                {phase === 'cancelling' ? '取消中' : '停止'}
                <Square className="h-3.5 w-3.5" />
              </Button>
            )}
          </div>
        </div>
      </footer>
    </main>
  )
}

function ContextRail({
  workspace,
  documents,
  employee,
  identity,
  modelIdentity,
  gear,
  usage,
  timelineOpen,
  session,
  modelSettings,
  onTimeline,
  onConfigureModel,
  filePanel,
}: {
  workspace?: WorkspaceRead
  documents: Array<{ id: string; filename: string; status: string }>
  employee: EmployeeDefinition
  identity: string
  modelIdentity: P6ModelIdentity
  gear: P6ReasoningGear
  usage: AgentAlphaUsage | null
  timelineOpen: boolean
  session: WorkbenchSession
  modelSettings: AgentModelSettingRead[]
  onTimeline: () => void
  onConfigureModel: (employee: EmployeeDefinition) => void
  filePanel: React.ReactNode
}) {
  const provider = getP6ProviderProfile(modelIdentity)
  const selectedGear = P6_GEAR_PROFILES[gear]
  const cost = estimateP6Cost({
    inputTokens: usage?.input_tokens ?? 0,
    outputTokens: usage?.output_tokens ?? 0,
    reasoningTokens: usage?.reasoning_tokens,
  })
  return (
    <aside className="hidden min-h-0 flex-col border-l bg-muted/10 lg:flex">
      <div className="flex h-12 items-center justify-between border-b px-3">
        <span className="text-[10px] font-semibold">工作上下文</span>
        <button
          type="button"
          onClick={onTimeline}
          className="rounded p-1.5 text-muted-foreground hover:bg-muted"
        >
          {timelineOpen ? <X className="h-3.5 w-3.5" /> : <History className="h-3.5 w-3.5" />}
        </button>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto p-3">
        {timelineOpen ? (
          <div className="space-y-3">
            <RailTitle icon={History} title="会话时间线" />
            {[...session.timeline].reverse().map((item) => (
              <div key={item.id} className="border-l pl-3">
                <p className="text-[9px] font-medium">{item.label}</p>
                <p className="mt-1 font-mono text-[7px] text-muted-foreground">
                  {new Date(item.createdAt).toLocaleString()}
                </p>
              </div>
            ))}
          </div>
        ) : (
          <div className="space-y-5">
            <RailSection icon={Folder} title="Workspace">
              <div className="rounded-xl border bg-background p-3">
                <p className="truncate text-[10px] font-semibold">
                  {workspace?.display_name ?? '未选择 Workspace'}
                </p>
                <p className="mt-1 text-[8px] text-muted-foreground">
                  {workspace
                    ? `generation ${workspace.generation} · ${workspace.observed_state}`
                    : '选择后加载上下文'}
                </p>
              </div>
            </RailSection>
            <RailSection icon={FileText} title="文件与知识">
              {documents.slice(0, 4).map((document) => (
                <div
                  key={document.id}
                  className="mb-1 flex items-center gap-2 rounded-lg px-2 py-2 text-[9px]"
                >
                  <FileText className="h-3.5 w-3.5" />
                  <span className="min-w-0 flex-1 truncate">{document.filename}</span>
                  <span className="font-mono text-[6px] uppercase text-muted-foreground">
                    {document.status}
                  </span>
                </div>
              ))}
              {filePanel}
            </RailSection>
            <RailSection icon={Users} title="预制员工">
              <div className="space-y-1">
                {PERSONAL_EMPLOYEES.map((item) => (
                  <div
                    key={item.id}
                    className="flex items-center gap-2 rounded-lg px-2 py-1.5 text-[9px]"
                  >
                    <span
                      className={cn(
                        'h-1.5 w-1.5 rounded-full',
                        item.id === employee.id
                          ? 'bg-foreground'
                          : 'border border-muted-foreground/50',
                      )}
                    />
                    <span className="min-w-0 flex-1">
                      <span className="block truncate">{item.displayName}</span>
                      <span className="block truncate font-mono text-[6px] text-muted-foreground">
                        {modelSettings.find((setting) => setting.employee_role_id === item.id)
                          ?.effective_model_id ?? '未配置可用模型'}
                      </span>
                    </span>
                    <span className="font-mono text-[6px] uppercase text-muted-foreground">
                      {(() => {
                        const setting = modelSettings.find(
                          (candidate) => candidate.employee_role_id === item.id,
                        )
                        if (!setting) return '加载中'
                        if (setting.state === 'pending') return '待验证'
                        if (setting.state === 'unavailable') return '不可用'
                        return setting.inherit_default ? '继承' : '专属'
                      })()}
                    </span>
                    <button
                      type="button"
                      aria-label={`配置 ${item.displayName} 的模型`}
                      onClick={() => onConfigureModel(item)}
                      className="rounded p-1 text-muted-foreground hover:bg-muted hover:text-foreground"
                    >
                      <Cog className="h-3 w-3" />
                    </button>
                  </div>
                ))}
              </div>
            </RailSection>
            <RailSection icon={CircleDot} title="模型与成本">
              <p className="break-words text-[8px] leading-4 text-muted-foreground">{identity}</p>
              <p className="mt-1 text-[8px] font-medium">
                最近观测：{provider.displayName} · {selectedGear.displayName}
              </p>
              <p className="mt-1 text-[7px] leading-3 text-muted-foreground">
                按当前角色的有效模型名称选择保守画像；名称识别不是原生能力证明。原生思考参数未接入；
                Tools / MCP / CLI / Vision / 自主委派均关闭。目标输出{' '}
                {selectedGear.targetOutputTokens.toLocaleString()} tokens 仅作界面预算，当前 API
                未下发该参数。
              </p>
              <div className="mt-2 grid grid-cols-3 gap-1">
                <Metric label="Input" value={usage?.input_tokens ?? 0} />
                <Metric label="Output" value={usage?.output_tokens ?? 0} />
                <Metric label="Total" value={usage?.total_tokens ?? 0} />
              </div>
              <p className="mt-2 text-[7px] text-muted-foreground">
                {cost.known
                  ? `${cost.currency} ${cost.amount.toFixed(6)}`
                  : '费用未知（未配置费率）'}
              </p>
            </RailSection>
            <div className="rounded-lg border p-2.5 text-[8px] leading-4 text-muted-foreground">
              <ShieldCheck className="mb-1 h-3.5 w-3.5" />
              会话保存在当前浏览器的 tenant/user 隔离空间；不保存访问令牌、Provider Key 或
              Capability。
            </div>
          </div>
        )}
      </div>
    </aside>
  )
}

function RailTitle({
  icon: Icon,
  title,
}: {
  icon: React.ComponentType<{ className?: string }>
  title: string
}) {
  return (
    <div className="flex items-center gap-2 text-[10px] font-semibold">
      <Icon className="h-3.5 w-3.5" />
      {title}
    </div>
  )
}
function RailSection({
  icon,
  title,
  children,
}: {
  icon: React.ComponentType<{ className?: string }>
  title: string
  children: React.ReactNode
}) {
  return (
    <section>
      <RailTitle icon={icon} title={title} />
      <div className="mt-2">{children}</div>
    </section>
  )
}
function Metric({ label, value }: { label: string; value: number }) {
  return (
    <div className="rounded-lg border px-2 py-2 text-center">
      <p className="font-mono text-[6px] uppercase text-muted-foreground">{label}</p>
      <p className="mt-1 text-[9px] font-semibold">{value}</p>
    </div>
  )
}
