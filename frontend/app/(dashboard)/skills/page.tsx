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
import { Input } from '@/components/ui/input'
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
import type { AgentModelSettingRead, P6EmployeeRoleId, ProviderRuntimePosture } from '@/lib/types'

interface WorkspaceOption {
  readonly id: string
  readonly display_name: string
}
interface AgentOption {
  readonly agent_version_id: string
  readonly display_name: string
}

const ROLE_LABELS: Record<P6EmployeeRoleId, string> = {
  parent: '父 Agent',
  product: '产品',
  ux: 'UX',
  frontend: '前端',
  backend: '后端',
  data: '数据',
  security: '安全',
  qa: '测试',
  operations: '运维',
  docs: '文档',
}

const CATEGORY_LABELS: Record<string, string> = {
  api: 'API',
  context: '上下文',
  data: '数据',
  dependency: '依赖',
  documentation: '文档',
  engineering: '工程',
  observability: '可观测性',
  performance: '性能',
  planning: '规划',
  release: '发布',
  research: '研究',
  security: '安全',
  testing: '测试',
  ux: 'UX',
}

export default function SkillsPage() {
  const [skills, setSkills] = useState<NativeSkillRead[]>([])
  const [workspaces, setWorkspaces] = useState<WorkspaceOption[]>([])
  const [agents, setAgents] = useState<AgentOption[]>([])
  const [installations, setInstallations] = useState<SkillInstallationRead[]>([])
  const [modelSettings, setModelSettings] = useState<AgentModelSettingRead[]>([])
  const [runtimePosture, setRuntimePosture] = useState<ProviderRuntimePosture | null>(null)
  const [scanReport, setScanReport] = useState<P6SkillScanReport | null>(null)
  const [catalogTotal, setCatalogTotal] = useState(0)
  const [catalogDigest, setCatalogDigest] = useState('')
  const [categories, setCategories] = useState<string[]>([])
  const [skillQuery, setSkillQuery] = useState('')
  const [categoryFilter, setCategoryFilter] = useState('all')
  const [roleFilter, setRoleFilter] = useState<'all' | P6EmployeeRoleId>('all')
  const [liveSkillCount, setLiveSkillCount] = useState(0)
  const [liveInstructionBytes, setLiveInstructionBytes] = useState(0)
  const [maxLiveSkills, setMaxLiveSkills] = useState(8)
  const [maxInstructionBytes, setMaxInstructionBytes] = useState(32_768)
  const [workspaceId, setWorkspaceId] = useState('')
  const [agentVersionId, setAgentVersionId] = useState('')
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const refreshCapabilities = useCallback(async () => {
    if (!workspaceId || !agentVersionId) {
      setInstallations([])
      setModelSettings([])
      setLiveSkillCount(0)
      setLiveInstructionBytes(0)
      return
    }
    const [installationList, settingList] = await Promise.all([
      nativeSkillsApi.installations(workspaceId, agentVersionId),
      agentAlphaApi.modelSettings(workspaceId, agentVersionId),
    ])
    setInstallations(installationList.items)
    setModelSettings(settingList.items)
    setLiveSkillCount(installationList.live_count)
    setLiveInstructionBytes(installationList.live_instruction_bytes)
    setMaxLiveSkills(installationList.max_live_installations)
    setMaxInstructionBytes(installationList.max_instruction_bytes)
  }, [agentVersionId, workspaceId])

  useEffect(() => {
    Promise.all([workspacesApi.list(), userSettingsApi.runtime()])
      .then(([workspaceList, posture]) => {
        setWorkspaces(workspaceList.items)
        setRuntimePosture(posture)
        if (workspaceList.items.length === 1) setWorkspaceId(workspaceList.items[0]?.id ?? '')
      })
      .catch((reason) => setError(getApiErrorMessage(reason, '个人能力中心加载失败')))
  }, [])

  useEffect(() => {
    let cancelled = false
    const timer = window.setTimeout(() => {
      nativeSkillsApi
        .list({
          q: skillQuery.trim() || undefined,
          category: categoryFilter === 'all' ? undefined : categoryFilter,
          role: roleFilter === 'all' ? undefined : roleFilter,
        })
        .then((catalog) => {
          if (cancelled) return
          setSkills(catalog.items)
          setCatalogTotal(catalog.catalog_total)
          setCatalogDigest(catalog.catalog_digest)
          setCategories(catalog.categories)
        })
        .catch((reason) => {
          if (!cancelled) setError(getApiErrorMessage(reason, 'Skill 目录加载失败'))
        })
    }, 150)
    return () => {
      cancelled = true
      window.clearTimeout(timer)
    }
  }, [categoryFilter, roleFilter, skillQuery])

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

      <section className="grid gap-3 rounded-lg border p-4 lg:grid-cols-[minmax(0,1fr)_14rem_14rem]">
        <Input
          value={skillQuery}
          onChange={(event) => setSkillQuery(event.target.value.slice(0, 80))}
          placeholder="搜索名称、说明、标签或稳定标识"
          aria-label="搜索第一方 Skill"
        />
        <Select value={categoryFilter} onValueChange={setCategoryFilter}>
          <SelectTrigger aria-label="按分类筛选 Skill">
            <SelectValue placeholder="全部分类" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">全部分类</SelectItem>
            {categories.map((category) => (
              <SelectItem key={category} value={category}>
                {CATEGORY_LABELS[category] ?? category}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Select
          value={roleFilter}
          onValueChange={(value) => setRoleFilter(value as 'all' | P6EmployeeRoleId)}
        >
          <SelectTrigger aria-label="按推荐角色筛选 Skill">
            <SelectValue placeholder="全部推荐角色" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">全部推荐角色</SelectItem>
            {Object.entries(ROLE_LABELS).map(([role, label]) => (
              <SelectItem key={role} value={role}>
                {label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <div className="text-xs text-muted-foreground lg:col-span-3">
          当前显示 {skills.length} / {catalogTotal || 15} · 目录摘要{' '}
          {catalogDigest ? `${catalogDigest.slice(0, 16)}…` : '加载中'}
        </div>
      </section>

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
        <div className="flex flex-col justify-between gap-2 sm:flex-row sm:items-end">
          <div>
            <h2 className="text-xl font-semibold">OmniBase 第一方原生 Skills</h2>
            <p className="mt-1 text-sm text-muted-foreground">
              固定第一方目录，不接受第三方上传、URL、ZIP、脚本或 Marketplace。
            </p>
          </div>
          <div className="text-sm text-muted-foreground">
            已安装 {liveSkillCount} / {maxLiveSkills} · 指令{' '}
            {(liveInstructionBytes / 1024).toFixed(1)} / {(maxInstructionBytes / 1024).toFixed(0)}{' '}
            KiB
          </div>
        </div>
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {skills.map((skill) => {
            const installed = liveByKey.get(skill.stable_logical_key)
            const working = busy === skill.stable_logical_key
            const budgetBlocked =
              liveSkillCount >= maxLiveSkills ||
              liveInstructionBytes + skill.instructions_bytes > maxInstructionBytes
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
                    <Badge variant="secondary">
                      {CATEGORY_LABELS[skill.category] ?? skill.category}
                    </Badge>
                  </div>
                  <div className="space-y-2 text-xs text-muted-foreground">
                    <p>
                      推荐：{skill.recommended_roles.map((role) => ROLE_LABELS[role]).join('、')}
                    </p>
                    <div className="flex flex-wrap gap-1">
                      {skill.tags.map((tag) => (
                        <Badge key={tag} variant="outline" className="font-normal">
                          {tag}
                        </Badge>
                      ))}
                    </div>
                    <p>指令体积：{skill.instructions_bytes} bytes</p>
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
                      disabled={!workspaceId || !agentVersionId || working || budgetBlocked}
                    >
                      {working && <Loader2 className="h-4 w-4 animate-spin" />}
                      {budgetBlocked ? '安装预算已满' : '安装到当前 AI 员工'}
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
