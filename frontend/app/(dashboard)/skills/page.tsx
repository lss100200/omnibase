'use client'

import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  BookOpenCheck,
  Bot,
  FolderSearch,
  Loader2,
  Plug,
  ShieldCheck,
  Sparkles,
} from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import {
  agentAlphaApi,
  getApiErrorMessage,
  nativeSkillsApi,
  type NativeSkillRead,
  type SkillInstallationRead,
  userSettingsApi,
  workspacesApi,
} from '@/lib/api'
import { P6_READONLY_MCP_TOOLS, summarizeP6ModelCapabilities } from '@/lib/p6-capability-center'
import { chooseAndScanP6SkillRoot } from '@/lib/p6-skill-browser'
import type { P6SkillScanReport } from '@/lib/p6-skill-discovery'
import type { AgentModelSettingRead, ProviderRuntimePosture } from '@/lib/types'

interface WorkspaceOption {
  readonly id: string
  readonly display_name: string
}
interface AgentOption {
  readonly agent_version_id: string
  readonly display_name: string
}

export default function SkillsPage() {
  const [skills, setSkills] = useState<NativeSkillRead[]>([])
  const [workspaces, setWorkspaces] = useState<WorkspaceOption[]>([])
  const [agents, setAgents] = useState<AgentOption[]>([])
  const [installations, setInstallations] = useState<SkillInstallationRead[]>([])
  const [modelSettings, setModelSettings] = useState<AgentModelSettingRead[]>([])
  const [runtimePosture, setRuntimePosture] = useState<ProviderRuntimePosture | null>(null)
  const [scanReport, setScanReport] = useState<P6SkillScanReport | null>(null)
  const [workspaceId, setWorkspaceId] = useState('')
  const [agentVersionId, setAgentVersionId] = useState('')
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const refreshCapabilities = useCallback(async () => {
    if (!workspaceId || !agentVersionId) {
      setInstallations([])
      setModelSettings([])
      return
    }
    const [installationList, settingList] = await Promise.all([
      nativeSkillsApi.installations(workspaceId, agentVersionId),
      agentAlphaApi.modelSettings(workspaceId, agentVersionId),
    ])
    setInstallations(installationList.items)
    setModelSettings(settingList.items)
  }, [agentVersionId, workspaceId])

  useEffect(() => {
    Promise.all([nativeSkillsApi.list(), workspacesApi.list(), userSettingsApi.runtime()])
      .then(([catalog, workspaceList, posture]) => {
        setSkills(catalog.items)
        setWorkspaces(workspaceList.items)
        setRuntimePosture(posture)
        if (workspaceList.items.length === 1) setWorkspaceId(workspaceList.items[0]?.id ?? '')
      })
      .catch((reason) => setError(getApiErrorMessage(reason, '个人能力中心加载失败')))
  }, [])

  useEffect(() => {
    setAgents([])
    setAgentVersionId('')
    if (!workspaceId) return
    agentAlphaApi
      .profiles(workspaceId)
      .then((profiles) => {
        setAgents(profiles.items)
        if (profiles.items.length === 1)
          setAgentVersionId(profiles.items[0]?.agent_version_id ?? '')
      })
      .catch((reason) => setError(getApiErrorMessage(reason, 'AI 员工列表加载失败')))
  }, [workspaceId])

  useEffect(() => {
    refreshCapabilities().catch((reason) =>
      setError(getApiErrorMessage(reason, '能力状态加载失败')),
    )
  }, [refreshCapabilities])

  const liveByKey = useMemo(
    () =>
      new Map(
        installations
          .filter((item) => item.installation_state === 'installed')
          .map((item) => [item.stable_logical_key, item]),
      ),
    [installations],
  )
  const modelSummary = runtimePosture
    ? summarizeP6ModelCapabilities(runtimePosture, modelSettings)
    : null

  const install = async (skill: NativeSkillRead) => {
    if (!workspaceId || !agentVersionId || busy) return
    setBusy(skill.stable_logical_key)
    setError(null)
    try {
      await nativeSkillsApi.install(skill, workspaceId, agentVersionId)
      await refreshCapabilities()
    } catch (reason) {
      setError(getApiErrorMessage(reason, '技能安装失败'))
    } finally {
      setBusy(null)
    }
  }

  const disable = async (installation: SkillInstallationRead) => {
    if (busy) return
    setBusy(installation.stable_logical_key)
    setError(null)
    try {
      await nativeSkillsApi.disable(installation)
      await refreshCapabilities()
    } catch (reason) {
      setError(getApiErrorMessage(reason, '技能停用失败'))
    } finally {
      setBusy(null)
    }
  }

  const scanLocalSkills = async () => {
    if (busy) return
    setBusy('scan')
    setError(null)
    try {
      setScanReport(await chooseAndScanP6SkillRoot())
    } catch (reason) {
      if (!(reason instanceof DOMException && reason.name === 'AbortError'))
        setError(getApiErrorMessage(reason, '本机 Skill 扫描失败'))
    } finally {
      setBusy(null)
    }
  }

  return (
    <div className="mx-auto max-w-7xl space-y-6">
      <header className="flex flex-col justify-between gap-4 border-b pb-5 lg:flex-row lg:items-end">
        <div>
          <div className="flex items-center gap-2">
            <Sparkles className="h-6 w-6" />
            <h1 className="text-2xl font-semibold">个人能力中心</h1>
          </div>
          <p className="mt-2 max-w-3xl text-sm leading-6 text-muted-foreground">
            管理第一方纯指令 Skill，查看一父九子的模型配置，并安全预检本机
            Skill。扫描不会执行、安装或联网；只读 MCP 仍未接入 Agent Runtime。
          </p>
        </div>
        <div className="grid gap-2 sm:grid-cols-2">
          <Select value={workspaceId} onValueChange={setWorkspaceId}>
            <SelectTrigger>
              <SelectValue placeholder="选择 AI 空间" />
            </SelectTrigger>
            <SelectContent>
              {workspaces.map((workspace) => (
                <SelectItem key={workspace.id} value={workspace.id}>
                  {workspace.display_name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Select value={agentVersionId} onValueChange={setAgentVersionId} disabled={!workspaceId}>
            <SelectTrigger>
              <SelectValue placeholder="选择 AI 员工" />
            </SelectTrigger>
            <SelectContent>
              {agents.map((agent) => (
                <SelectItem key={agent.agent_version_id} value={agent.agent_version_id}>
                  {agent.display_name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      </header>

      {error && (
        <div className="border border-destructive/50 px-4 py-3 text-sm text-destructive">
          {error}
        </div>
      )}

      <section className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-lg">
              <Bot className="h-5 w-5" />
              模型矩阵
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-2 text-sm">
            <p>默认来源：{modelSummary?.defaultRuntimeSource ?? '等待选择'}</p>
            <p>
              可用角色：{modelSummary?.readyRoles ?? 0} / {modelSummary?.roleTotal ?? 10}
            </p>
            <p>
              待验证：{modelSummary?.pendingRoles ?? 0} · 不可用：
              {modelSummary?.unavailableRoles ?? 0}
            </p>
            <p>专属覆盖：{modelSummary?.explicitOverrides ?? 0}</p>
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-lg">
              <Plug className="h-5 w-5" />
              只读 MCP 预览
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-2 text-sm">
            {P6_READONLY_MCP_TOOLS.map((tool) => (
              <p key={tool.id}>
                <code>{tool.id}</code> · {tool.label}
              </p>
            ))}
            <Badge variant="outline">未接入 Agent Alpha</Badge>
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-lg">
              <FolderSearch className="h-5 w-5" />
              本机 Skill 安全预检
            </CardTitle>
            <CardDescription>只扫描你主动选择的目录；未知 Skill 不可安装。</CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            <Button
              variant="outline"
              className="w-full"
              onClick={() => void scanLocalSkills()}
              disabled={busy !== null}
            >
              {busy === 'scan' && <Loader2 className="h-4 w-4 animate-spin" />}选择目录并扫描
            </Button>
            <p className="text-xs text-muted-foreground">
              零执行 · 零安装 · 零网络 · 不返回物理路径
            </p>
          </CardContent>
        </Card>
      </section>

      {scanReport && (
        <section className="space-y-3">
          <div className="flex items-center justify-between">
            <h2 className="text-xl font-semibold">扫描结果</h2>
            <Badge variant="outline">{scanReport.candidates.length} 个候选</Badge>
          </div>
          {scanReport.candidates.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              所选目录下没有发现直接包含 SKILL.md 的候选目录。
            </p>
          ) : (
            <div className="grid gap-3 md:grid-cols-2">
              {scanReport.candidates.map((candidate) => (
                <Card key={candidate.sourceId}>
                  <CardHeader>
                    <div className="flex items-start justify-between gap-3">
                      <CardTitle className="text-base">{candidate.displayName}</CardTitle>
                      <Badge variant={candidate.status === 'rejected' ? 'destructive' : 'outline'}>
                        {candidate.status === 'rejected' ? '已拒绝' : '未审阅'}
                      </Badge>
                    </div>
                    <CardDescription>{candidate.description}</CardDescription>
                  </CardHeader>
                  <CardContent className="space-y-2 text-xs text-muted-foreground">
                    <p>SHA-256：{candidate.digest.slice(0, 20)}…</p>
                    {candidate.blockers.map((blocker) => (
                      <p key={blocker}>• {blocker}</p>
                    ))}
                    <p>当前版本不会安装或执行此候选。</p>
                  </CardContent>
                </Card>
              ))}
            </div>
          )}
        </section>
      )}

      <section className="space-y-3">
        <h2 className="text-xl font-semibold">OmniBase 第一方原生 Skills</h2>
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {skills.map((skill) => {
            const installed = liveByKey.get(skill.stable_logical_key)
            const working = busy === skill.stable_logical_key
            return (
              <Card key={skill.stable_logical_key} className="flex min-h-64 flex-col">
                <CardHeader>
                  <div className="flex items-start justify-between gap-3">
                    <div className="rounded-lg border p-2">
                      <BookOpenCheck className="h-5 w-5" />
                    </div>
                    <Badge variant={installed ? 'secondary' : 'outline'}>
                      {installed ? '已安装' : '可安装'}
                    </Badge>
                  </div>
                  <CardTitle className="pt-2 text-lg">{skill.display_name}</CardTitle>
                  <CardDescription className="leading-5">{skill.description}</CardDescription>
                </CardHeader>
                <CardContent className="mt-auto space-y-4">
                  <div className="flex flex-wrap gap-2 text-xs">
                    <Badge variant="outline">纯指令</Badge>
                    <Badge variant="outline">无工具</Badge>
                    <Badge variant="outline">无网络</Badge>
                    <Badge variant="outline">无密钥</Badge>
                  </div>
                  <div className="flex items-center gap-2 text-xs text-muted-foreground">
                    <ShieldCheck className="h-4 w-4" />
                    <span>
                      v{skill.semantic_version} · {skill.manifest_digest.slice(0, 12)}…
                    </span>
                  </div>
                  {installed ? (
                    <Button
                      variant="outline"
                      className="w-full"
                      onClick={() => void disable(installed)}
                      disabled={working}
                    >
                      {working && <Loader2 className="h-4 w-4 animate-spin" />}停用
                    </Button>
                  ) : (
                    <Button
                      className="w-full"
                      onClick={() => void install(skill)}
                      disabled={!workspaceId || !agentVersionId || working}
                    >
                      {working && <Loader2 className="h-4 w-4 animate-spin" />}安装到当前 AI 员工
                    </Button>
                  )}
                </CardContent>
              </Card>
            )
          })}
        </div>
      </section>
    </div>
  )
}
