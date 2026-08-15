'use client'

import { useMemo, useRef, useState } from 'react'
import { Download, LoaderCircle, Play, RotateCcw, ShieldCheck, Square, Users } from 'lucide-react'
import { toast } from 'sonner'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { agentAlphaApi, getApiErrorMessage } from '@/lib/api'
import { isUserCancelledError } from '@/lib/cancel-detection'
import {
  parseP6PracticeOutput,
  type P6PracticeOutput,
  type P6PracticeWorkspaceProposal,
} from '@/lib/p6-practice-output'
import {
  consumeP6PracticeStream,
  type P6PracticeNodeReceipt,
  type P6PracticeScenario,
} from '@/lib/p6-practice-stream'
import type { P6EmployeeRoleId } from '@/lib/types'

import type { P6TaskBinding } from './workspace-file-panel'

const COUNTS = [1, 3, 4, 5, 6] as const

const ROSTERS: Record<P6PracticeScenario, readonly Exclude<P6EmployeeRoleId, 'parent'>[]> = {
  rag: ['data', 'qa', 'security', 'docs', 'operations'],
  artifact: ['product', 'ux', 'frontend', 'qa', 'security'],
  workspace: ['product', 'frontend', 'backend', 'security', 'qa'],
}

const SCENARIOS: Record<
  P6PracticeScenario,
  { readonly label: string; readonly defaultTask: string }
> = {
  rag: {
    label: '文件 RAG 与引用',
    defaultTask:
      '根据当前 Workspace 已上传并完成索引的文件回答问题；每一条事实都必须使用 [n] 引用。',
  },
  artifact: {
    label: '时钟 / HTML 演示文稿',
    defaultTask: '生成一个可以离线打开、无外部依赖的时钟或 HTML 演示文稿规格。',
  },
  workspace: {
    label: 'Workspace 修改提案',
    defaultTask: '对已由 Owner 授权并在文件树中打开的一个 UTF-8 文件提出最小完整替换。',
  },
}

type PracticePhase = 'idle' | 'running' | 'cancelling' | 'done' | 'error'

interface Props {
  readonly workspaceId: string
  readonly agentVersionId: string
  readonly disabled: boolean
  readonly practiceActive: boolean
  readonly onTaskBinding: (binding: P6TaskBinding) => void
  readonly onRunningChange: (running: boolean) => void
  readonly onLoadWorkspaceProposal: (
    proposal: P6PracticeWorkspaceProposal['change'],
  ) => Promise<void>
}

function roleLabel(role: P6EmployeeRoleId): string {
  const labels: Record<P6EmployeeRoleId, string> = {
    parent: '父 Agent',
    product: '产品',
    ux: 'UX',
    frontend: '前端',
    backend: '后端',
    data: '数据',
    security: '安全',
    qa: 'QA',
    operations: '运维',
    docs: '文档',
  }
  return labels[role]
}

function downloadArtifact(output: Extract<P6PracticeOutput, { kind: 'artifact' }>): void {
  const url = URL.createObjectURL(new Blob([output.html], { type: output.mediaType }))
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = output.filename
  anchor.click()
  URL.revokeObjectURL(url)
}

export function PersonalAgentPracticePanel({
  workspaceId,
  agentVersionId,
  disabled,
  practiceActive,
  onTaskBinding,
  onRunningChange,
  onLoadWorkspaceProposal,
}: Props) {
  const [scenario, setScenario] = useState<P6PracticeScenario>('rag')
  const [participantCount, setParticipantCount] = useState<(typeof COUNTS)[number]>(1)
  const [task, setTask] = useState(SCENARIOS.rag.defaultTask)
  const [phase, setPhase] = useState<PracticePhase>('idle')
  const [nodes, setNodes] = useState<P6PracticeNodeReceipt[]>([])
  const [activeRole, setActiveRole] = useState<P6EmployeeRoleId | null>(null)
  const [output, setOutput] = useState<P6PracticeOutput | null>(null)
  const [errorCode, setErrorCode] = useState<string | null>(null)
  const controllerRef = useRef<AbortController | null>(null)
  const currentInvocationRef = useRef<string | null>(null)
  const currentWorkspaceRef = useRef('')
  const roster = useMemo(
    () => [...ROSTERS[scenario].slice(0, participantCount - 1), 'parent'] as P6EmployeeRoleId[],
    [participantCount, scenario],
  )
  const running = phase === 'running' || phase === 'cancelling'

  async function run(): Promise<void> {
    if (running || disabled || !practiceActive || !workspaceId || !agentVersionId || !task.trim()) {
      return
    }
    const controller = new AbortController()
    controllerRef.current = controller
    currentInvocationRef.current = null
    currentWorkspaceRef.current = workspaceId
    setPhase('running')
    onRunningChange(true)
    setNodes([])
    setActiveRole(null)
    setOutput(null)
    setErrorCode(null)
    try {
      const specialistRoles = roster.slice(0, -1) as Exclude<P6EmployeeRoleId, 'parent'>[]
      const response = await agentAlphaApi.practiceStream(
        workspaceId,
        {
          agent_version_id: agentVersionId,
          scenario,
          participant_count: participantCount,
          specialist_roles: specialistRoles,
          task: task.trim(),
          top_k: 5,
        },
        { signal: controller.signal },
      )
      if (!response.ok || !response.body) throw new Error(`p6_practice_http_${response.status}`)
      const terminal = await consumeP6PracticeStream(response.body.getReader(), {
        onNodeStarted: ({ role }) => {
          currentInvocationRef.current = null
          setActiveRole(role)
        },
        onNodeIdentity: ({ invocationId }) => {
          currentInvocationRef.current = invocationId
        },
        onNodeCompleted: (receipt) => {
          currentInvocationRef.current = null
          setNodes((current) => [...current, receipt])
          setActiveRole(null)
        },
      })
      if (terminal.kind === 'cancelled') {
        setPhase('idle')
        toast.info('受控协作已取消')
        return
      }
      if (terminal.kind === 'error') {
        setErrorCode(terminal.code)
        setPhase('error')
        return
      }
      const parsed = await parseP6PracticeOutput(terminal.scenario, terminal.finalAnswer)
      onTaskBinding({
        taskId: terminal.parentTaskId,
        invocationId: terminal.parentInvocationId,
      })
      setOutput(parsed)
      setPhase('done')
    } catch (error) {
      if (isUserCancelledError(error)) {
        setPhase('idle')
        toast.info('本地流已中断；服务器账本将收敛当前节点状态')
      } else {
        setErrorCode(getApiErrorMessage(error, 'p6_practice_failed'))
        setPhase('error')
      }
    } finally {
      controllerRef.current = null
      currentInvocationRef.current = null
      currentWorkspaceRef.current = ''
      setActiveRole(null)
      onRunningChange(false)
    }
  }

  async function stop(): Promise<void> {
    if (!controllerRef.current) return
    setPhase('cancelling')
    const invocationId = currentInvocationRef.current
    if (invocationId && currentWorkspaceRef.current) {
      await agentAlphaApi.cancel(currentWorkspaceRef.current, invocationId).catch(() => undefined)
    }
    controllerRef.current.abort()
  }

  return (
    <div className="rounded-xl border bg-background p-3">
      <div className="flex items-start justify-between gap-2">
        <div>
          <p className="text-sm font-semibold">P6.4 个人受控协作</p>
          <p className="mt-1 text-xs leading-5 text-muted-foreground">
            独立、串行、逐节点计量；企业 Planner / Multi-Agent / MCP 未启用。
          </p>
        </div>
        <Badge variant={practiceActive ? 'default' : 'outline'} className="shrink-0 text-xs">
          {practiceActive ? '生产窗口已打开' : '生产窗口关闭'}
        </Badge>
      </div>

      <div className="mt-3 grid grid-cols-2 gap-2">
        <label className="space-y-1 text-xs">
          <span className="text-muted-foreground">场景</span>
          <select
            value={scenario}
            disabled={running || disabled}
            onChange={(event) => {
              const next = event.target.value as P6PracticeScenario
              setScenario(next)
              setTask(SCENARIOS[next].defaultTask)
              setOutput(null)
              setNodes([])
              setPhase('idle')
            }}
            className="h-9 w-full rounded-md border bg-background px-2 text-xs"
          >
            {Object.entries(SCENARIOS).map(([value, item]) => (
              <option key={value} value={value}>
                {item.label}
              </option>
            ))}
          </select>
        </label>
        <label className="space-y-1 text-xs">
          <span className="text-muted-foreground">参与者</span>
          <select
            value={participantCount}
            disabled={running || disabled}
            onChange={(event) =>
              setParticipantCount(Number(event.target.value) as (typeof COUNTS)[number])
            }
            className="h-9 w-full rounded-md border bg-background px-2 text-xs"
          >
            {COUNTS.map((count) => (
              <option key={count} value={count}>
                {count} Agent
              </option>
            ))}
          </select>
        </label>
      </div>

      <div className="mt-2 flex flex-wrap gap-1">
        {roster.map((role, index) => (
          <Badge
            key={role}
            variant={activeRole === role ? 'default' : 'outline'}
            className="text-xs"
          >
            {index + 1}. {roleLabel(role)}
          </Badge>
        ))}
      </div>

      <textarea
        value={task}
        disabled={running || disabled}
        onChange={(event) => setTask(event.target.value)}
        maxLength={16_000}
        rows={4}
        className="mt-3 w-full resize-y rounded-md border bg-background px-2 py-2 text-sm leading-6"
      />

      <div className="mt-2 flex gap-2">
        {running ? (
          <Button size="sm" variant="destructive" className="flex-1" onClick={() => void stop()}>
            {phase === 'cancelling' ? (
              <LoaderCircle className="h-4 w-4 animate-spin" />
            ) : (
              <Square className="h-4 w-4" />
            )}
            {phase === 'cancelling' ? '取消中' : '停止当前节点'}
          </Button>
        ) : (
          <Button
            size="sm"
            className="flex-1"
            disabled={
              disabled || !practiceActive || !workspaceId || !agentVersionId || !task.trim()
            }
            onClick={() => void run()}
          >
            <Play className="h-4 w-4" />
            启动 {participantCount} Agent
          </Button>
        )}
        {(phase === 'done' || phase === 'error') && !running ? (
          <Button
            size="sm"
            variant="outline"
            onClick={() => {
              setOutput(null)
              setNodes([])
              setErrorCode(null)
              setPhase('idle')
            }}
          >
            <RotateCcw className="h-4 w-4" />
          </Button>
        ) : null}
      </div>

      {nodes.length > 0 ? (
        <div className="mt-3 space-y-1.5">
          {nodes.map((node) => (
            <div key={node.invocationId} className="rounded-md border px-2 py-1.5 text-xs">
              <div className="flex items-center justify-between gap-2">
                <span className="font-medium">
                  {node.ordinal}. {roleLabel(node.role)}
                </span>
                <span className="font-mono text-muted-foreground">
                  {node.usage.total_tokens.toLocaleString()} tokens
                </span>
              </div>
              <p className="mt-1 truncate font-mono text-xs text-muted-foreground">
                {node.actualModelId}
              </p>
            </div>
          ))}
        </div>
      ) : null}

      {phase === 'error' && errorCode ? (
        <div className="mt-3 rounded-md border border-destructive/40 bg-destructive/5 p-2 text-xs leading-5 text-destructive">
          受控协作未完成：{errorCode}
        </div>
      ) : null}

      {output?.kind === 'rag' ? (
        <div className="mt-3 rounded-md border p-2 text-sm leading-6">
          <div className="mb-1 flex items-center gap-1.5 font-medium">
            <ShieldCheck className="h-4 w-4" />父 Agent 结果
          </div>
          <p className="whitespace-pre-wrap">{output.answer}</p>
          <p className="mt-2 text-xs text-muted-foreground">
            {output.claims.length} 条结构化 claim；最终精确率/召回率由验收 runner
            的确定性评分器计算。
          </p>
        </div>
      ) : null}

      {output?.kind === 'artifact' ? (
        <div className="mt-3 rounded-md border p-2 text-xs leading-5">
          <p className="font-medium">可信本地渲染：{output.filename}</p>
          <p className="font-mono text-muted-foreground">
            {output.byteLength.toLocaleString()} bytes · {output.sha256.slice(0, 12)}…
          </p>
          <Button
            size="sm"
            variant="outline"
            className="mt-2 w-full"
            onClick={() => downloadArtifact(output)}
          >
            <Download className="h-4 w-4" />
            下载离线 HTML
          </Button>
        </div>
      ) : null}

      {output?.kind === 'workspace' ? (
        <div className="mt-3 rounded-md border p-2 text-xs leading-5">
          <div className="flex items-center gap-1.5 font-medium">
            <Users className="h-4 w-4" />
            修改提案：{output.change.path}
          </div>
          <p className="mt-1 text-muted-foreground">{output.summary}</p>
          <Button
            size="sm"
            variant="outline"
            className="mt-2 w-full"
            onClick={() =>
              void onLoadWorkspaceProposal(output.change)
                .then(() => toast.success('提案已载入 Before/After 审阅区；尚未写入文件'))
                .catch((error) =>
                  toast.error('无法载入修改提案', {
                    description: getApiErrorMessage(error, 'p6_practice_proposal_rejected'),
                  }),
                )
            }
          >
            载入审阅区（不自动写入）
          </Button>
        </div>
      ) : null}
    </div>
  )
}
