'use client'

import { useCallback, useEffect, useMemo, useState } from 'react'
import { BookOpenCheck, Loader2, ShieldCheck, Sparkles } from 'lucide-react'
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
  workspacesApi,
} from '@/lib/api'

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
  const [workspaceId, setWorkspaceId] = useState('')
  const [agentVersionId, setAgentVersionId] = useState('')
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const refreshInstallations = useCallback(async () => {
    if (!workspaceId || !agentVersionId) {
      setInstallations([])
      return
    }
    const result = await nativeSkillsApi.installations(workspaceId, agentVersionId)
    setInstallations(result.items)
  }, [agentVersionId, workspaceId])

  useEffect(() => {
    Promise.all([nativeSkillsApi.list(), workspacesApi.list()])
      .then(([catalog, workspaceList]) => {
        setSkills(catalog.items)
        setWorkspaces(workspaceList.items)
        if (workspaceList.items.length === 1) setWorkspaceId(workspaceList.items[0]?.id ?? '')
      })
      .catch((reason) => setError(getApiErrorMessage(reason, '技能目录加载失败')))
  }, [])

  useEffect(() => {
    setAgents([])
    setAgentVersionId('')
    if (!workspaceId) return
    agentAlphaApi
      .profiles(workspaceId)
      .then((profiles) => {
        setAgents(profiles.items)
        if (profiles.items.length === 1) {
          setAgentVersionId(profiles.items[0]?.agent_version_id ?? '')
        }
      })
      .catch((reason) => setError(getApiErrorMessage(reason, 'AI 员工列表加载失败')))
  }, [workspaceId])

  useEffect(() => {
    refreshInstallations().catch((reason) =>
      setError(getApiErrorMessage(reason, '技能安装状态加载失败')),
    )
  }, [refreshInstallations])

  const liveByKey = useMemo(
    () =>
      new Map(
        installations
          .filter((item) => item.installation_state === 'installed')
          .map((item) => [item.stable_logical_key, item]),
      ),
    [installations],
  )

  const install = async (skill: NativeSkillRead) => {
    if (!workspaceId || !agentVersionId || busy) return
    setBusy(skill.stable_logical_key)
    setError(null)
    try {
      await nativeSkillsApi.install(skill, workspaceId, agentVersionId)
      await refreshInstallations()
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
      await refreshInstallations()
    } catch (reason) {
      setError(getApiErrorMessage(reason, '技能停用失败'))
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
            <h1 className="text-2xl font-semibold">原生技能</h1>
          </div>
          <p className="mt-2 max-w-3xl text-sm leading-6 text-muted-foreground">
            OmniBase 第一方技能只改变回答方式，不会获得工具、网络、密钥、MCP、规划器或多 Agent
            权限。
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
                    onClick={() => disable(installed)}
                    disabled={working}
                  >
                    {working && <Loader2 className="h-4 w-4 animate-spin" />}
                    停用
                  </Button>
                ) : (
                  <Button
                    className="w-full"
                    onClick={() => install(skill)}
                    disabled={!workspaceId || !agentVersionId || working}
                  >
                    {working && <Loader2 className="h-4 w-4 animate-spin" />}
                    安装到当前 AI 员工
                  </Button>
                )}
              </CardContent>
            </Card>
          )
        })}
      </div>
    </div>
  )
}
