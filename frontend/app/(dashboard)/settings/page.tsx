'use client'

import Link from 'next/link'
import { useCallback, useEffect, useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import {
  Bot,
  CheckCircle2,
  KeyRound,
  Loader2,
  Network,
  Plus,
  RotateCw,
  Save,
  ShieldCheck,
  Trash2,
  UserRound,
} from 'lucide-react'
import { useAuth } from '@/lib/hooks/use-auth'
import { getApiErrorMessage, userSettingsApi } from '@/lib/api'
import type { ProviderCredentialRead, ProviderRuntimePosture, UserProfileRead } from '@/lib/types'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'

const providerPresets = {
  deepseek: {
    name: 'DeepSeek',
    baseUrl: 'https://api.deepseek.com/v1',
    modelId: 'deepseek-v4-flash',
  },
  zhipu: {
    name: '智谱 GLM',
    baseUrl: 'https://open.bigmodel.cn/api/paas/v4',
    modelId: 'glm-5.2',
  },
  dashscope: {
    name: 'DashScope',
    baseUrl: 'https://dashscope.aliyuncs.com/compatible-mode/v1',
    modelId: 'qwen3-32b',
  },
  openai: {
    name: 'OpenAI',
    baseUrl: 'https://api.openai.com/v1',
    modelId: 'gpt-5',
  },
} as const

type ProviderPreset = keyof typeof providerPresets

export default function SettingsPage() {
  const { user, tenant } = useAuth()
  const [profile, setProfile] = useState<UserProfileRead | null>(null)
  const [credentials, setCredentials] = useState<ProviderCredentialRead[]>([])
  const [runtime, setRuntime] = useState<ProviderRuntimePosture | null>(null)
  const [operatorFallback, setOperatorFallback] = useState(false)
  const [loading, setLoading] = useState(true)
  const [savingProfile, setSavingProfile] = useState(false)
  const [creatingCredential, setCreatingCredential] = useState(false)
  const [busyCredential, setBusyCredential] = useState<string | null>(null)
  const [message, setMessage] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [preset, setPreset] = useState<ProviderPreset>('deepseek')
  const [providerName, setProviderName] = useState<string>(providerPresets.deepseek.name)
  const [baseUrl, setBaseUrl] = useState<string>(providerPresets.deepseek.baseUrl)
  const [modelId, setModelId] = useState<string>(providerPresets.deepseek.modelId)
  const [apiKey, setApiKey] = useState('')

  const refresh = useCallback(async () => {
    const [profileValue, credentialValue, runtimeValue] = await Promise.all([
      userSettingsApi.profile(),
      userSettingsApi.credentials(),
      userSettingsApi.runtime(),
    ])
    setProfile(profileValue)
    setCredentials(credentialValue.items)
    setOperatorFallback(credentialValue.operator_fallback_available)
    setRuntime(runtimeValue)
  }, [])

  useEffect(() => {
    refresh()
      .catch((reason: unknown) => setError(getApiErrorMessage(reason, '设置加载失败')))
      .finally(() => setLoading(false))
  }, [refresh])

  const activeCredential = useMemo(
    () => credentials.find((credential) => credential.is_active && credential.is_default),
    [credentials],
  )

  const choosePreset = (value: ProviderPreset) => {
    const selected = providerPresets[value]
    setPreset(value)
    setProviderName(selected.name)
    setBaseUrl(selected.baseUrl)
    setModelId(selected.modelId)
  }

  const saveProfile = async () => {
    if (!profile || savingProfile) return
    setSavingProfile(true)
    setError(null)
    setMessage(null)
    try {
      const updated = await userSettingsApi.updateProfile({
        expected_version: profile.version,
        display_name: profile.display_name,
        locale: profile.locale,
        theme: profile.theme,
        assistant_name: profile.assistant_name,
        assistant_tone: profile.assistant_tone,
        assistant_instructions: profile.assistant_instructions,
      })
      setProfile(updated)
      setMessage('个人资料与 AI 个性设定已保存。')
    } catch (reason) {
      setError(getApiErrorMessage(reason, '保存失败'))
    } finally {
      setSavingProfile(false)
    }
  }

  const createCredential = async () => {
    if (!apiKey.trim() || creatingCredential) return
    setCreatingCredential(true)
    setError(null)
    setMessage(null)
    try {
      await userSettingsApi.createCredential({
        display_name: providerName,
        provider_id: preset,
        base_url: baseUrl,
        model_id: modelId,
        api_key: apiKey,
        is_default: true,
      })
      setApiKey('')
      await refresh()
      setMessage('密钥已加密保存。请先执行连接测试，通过后 Agent 才会使用它。')
    } catch (reason) {
      setError(getApiErrorMessage(reason, '密钥保存失败'))
    } finally {
      setCreatingCredential(false)
    }
  }

  const runCredentialAction = async (
    credential: ProviderCredentialRead,
    action: 'test' | 'activate' | 'revoke',
  ) => {
    setBusyCredential(credential.id)
    setError(null)
    setMessage(null)
    try {
      if (action === 'test') {
        const result = await userSettingsApi.test(credential.id)
        setMessage(
          result.status === 'passed'
            ? `连接通过：${result.actual_model_id} · ${result.latency_ms ?? '—'} ms`
            : `连接未通过：${result.status}`,
        )
      } else if (action === 'activate') {
        await userSettingsApi.activate(credential.id, credential.version)
        setMessage('已设为当前用户默认模型。')
      } else {
        await userSettingsApi.revoke(credential.id)
        setMessage('凭据已撤销，服务器端密文已清除。')
      }
      await refresh()
    } catch (reason) {
      setError(getApiErrorMessage(reason, '操作失败'))
    } finally {
      setBusyCredential(null)
    }
  }

  if (loading || !profile) {
    return (
      <div className="flex min-h-[60vh] items-center justify-center text-muted-foreground">
        <Loader2 className="mr-2 h-5 w-5 animate-spin" /> 正在加载个人工作台设置…
      </div>
    )
  }

  return (
    <div className="mx-auto max-w-6xl space-y-6 pb-12">
      <div className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
        <div>
          <div className="mb-2 flex items-center gap-2 text-sm font-medium text-primary">
            <ShieldCheck className="h-4 w-4" /> Personal control plane
          </div>
          <h1 className="text-3xl font-semibold tracking-tight">用户、AI 个性与模型连接</h1>
          <p className="mt-2 max-w-3xl text-sm leading-6 text-muted-foreground">
            这里保存真正的用户偏好和个人 Provider 凭据。API Key
            只以加密密文落库，浏览器不会再次读回明文。
          </p>
        </div>
        <Button asChild variant="outline">
          <Link href="/agents">
            <Bot className="h-4 w-4" /> 返回 Agent 工作台
          </Link>
        </Button>
      </div>

      {(message || error) && (
        <div
          className={`rounded-xl border px-4 py-3 text-sm ${
            error
              ? 'border-destructive/30 bg-destructive/10 text-destructive'
              : 'border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300'
          }`}
        >
          {error ?? message}
        </div>
      )}

      <Tabs defaultValue="profile" className="space-y-5">
        <TabsList className="grid h-auto w-full grid-cols-3 rounded-xl bg-muted/60 p-1 lg:w-[620px]">
          <TabsTrigger value="profile" className="gap-2 py-2.5">
            <UserRound className="h-4 w-4" /> 个人与个性
          </TabsTrigger>
          <TabsTrigger value="providers" className="gap-2 py-2.5">
            <KeyRound className="h-4 w-4" /> 模型与密钥
          </TabsTrigger>
          <TabsTrigger value="runtime" className="gap-2 py-2.5">
            <Network className="h-4 w-4" /> 运行姿态
          </TabsTrigger>
        </TabsList>

        <TabsContent value="profile" className="grid gap-5 lg:grid-cols-[1fr_360px]">
          <Card className="border-border/70">
            <CardHeader>
              <CardTitle>个人资料与 AI 个性</CardTitle>
              <CardDescription>这些设置属于当前用户，不会覆盖同一租户内其他成员。</CardDescription>
            </CardHeader>
            <CardContent className="space-y-5">
              <div className="grid gap-4 sm:grid-cols-2">
                <Field label="显示名称">
                  <Input
                    value={profile.display_name}
                    onChange={(event) =>
                      setProfile((current) =>
                        current ? { ...current, display_name: event.target.value } : current,
                      )
                    }
                  />
                </Field>
                <Field label="AI 助手名称">
                  <Input
                    value={profile.assistant_name}
                    onChange={(event) =>
                      setProfile((current) =>
                        current ? { ...current, assistant_name: event.target.value } : current,
                      )
                    }
                  />
                </Field>
                <Field label="界面语言">
                  <Select
                    value={profile.locale}
                    onValueChange={(value) =>
                      setProfile((current) => (current ? { ...current, locale: value } : current))
                    }
                  >
                    <SelectTrigger>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="zh-CN">简体中文</SelectItem>
                      <SelectItem value="en-US">English</SelectItem>
                    </SelectContent>
                  </Select>
                </Field>
                <Field label="回复风格">
                  <Select
                    value={profile.assistant_tone}
                    onValueChange={(value: UserProfileRead['assistant_tone']) =>
                      setProfile((current) =>
                        current ? { ...current, assistant_tone: value } : current,
                      )
                    }
                  >
                    <SelectTrigger>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="concise">简洁直接</SelectItem>
                      <SelectItem value="balanced">平衡清晰</SelectItem>
                      <SelectItem value="detailed">详细解释</SelectItem>
                    </SelectContent>
                  </Select>
                </Field>
              </div>
              <Field label="个性指令" hint={`${profile.assistant_instructions.length}/4000`}>
                <textarea
                  value={profile.assistant_instructions}
                  maxLength={4000}
                  rows={8}
                  onChange={(event) =>
                    setProfile((current) =>
                      current
                        ? { ...current, assistant_instructions: event.target.value }
                        : current,
                    )
                  }
                  placeholder="例如：默认使用中文；先给结论，再解释依据；不确定时明确说明。"
                  className="w-full resize-y rounded-lg border border-input bg-background px-3 py-3 text-sm outline-none ring-offset-background placeholder:text-muted-foreground focus-visible:ring-2 focus-visible:ring-ring"
                />
              </Field>
              <Button onClick={saveProfile} disabled={savingProfile}>
                {savingProfile ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <Save className="h-4 w-4" />
                )}
                保存设置
              </Button>
            </CardContent>
          </Card>

          <Card className="border-border/70 bg-muted/20">
            <CardHeader>
              <CardTitle className="text-base">当前账户</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4 text-sm">
              <InfoRow label="邮箱" value={user?.email ?? '—'} />
              <InfoRow label="租户" value={tenant?.name ?? '—'} />
              <InfoRow label="身份" value={user?.is_tenant_admin ? 'Tenant admin' : 'Member'} />
              <InfoRow label="Profile version" value={String(profile.version)} />
              <div className="rounded-lg border border-border/70 bg-background/70 p-3 text-xs leading-5 text-muted-foreground">
                Workspace 成员与角色在对应空间内管理；这里负责当前用户自己的身份表现和 AI 交互偏好。
              </div>
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="providers" className="space-y-5">
          <Card className="border-border/70">
            <CardHeader>
              <CardTitle>添加个人模型连接</CardTitle>
              <CardDescription>
                当前只允许受控的 OpenAI-compatible HTTPS 端点。保存后必须通过连接测试，Agent
                才会使用个人密钥。
              </CardDescription>
            </CardHeader>
            <CardContent className="grid gap-4 lg:grid-cols-2">
              <Field label="Provider">
                <Select
                  value={preset}
                  onValueChange={(value: ProviderPreset) => choosePreset(value)}
                >
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {Object.entries(providerPresets).map(([key, value]) => (
                      <SelectItem key={key} value={key}>
                        {value.name}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </Field>
              <Field label="显示名称">
                <Input
                  value={providerName}
                  onChange={(event) => setProviderName(event.target.value)}
                />
              </Field>
              <Field label="API Base URL">
                <Input value={baseUrl} onChange={(event) => setBaseUrl(event.target.value)} />
              </Field>
              <Field label="Model ID">
                <Input value={modelId} onChange={(event) => setModelId(event.target.value)} />
              </Field>
              <div className="lg:col-span-2">
                <Field label="API Key" hint="保存后不再回显">
                  <Input
                    type="password"
                    autoComplete="new-password"
                    value={apiKey}
                    onChange={(event) => setApiKey(event.target.value)}
                    placeholder="输入 Provider API Key"
                  />
                </Field>
              </div>
              <div className="lg:col-span-2">
                <Button onClick={createCredential} disabled={!apiKey.trim() || creatingCredential}>
                  {creatingCredential ? (
                    <Loader2 className="h-4 w-4 animate-spin" />
                  ) : (
                    <Plus className="h-4 w-4" />
                  )}
                  加密保存并设为默认
                </Button>
              </div>
            </CardContent>
          </Card>

          <div className="grid gap-4">
            {credentials.length === 0 ? (
              <Card className="border-dashed">
                <CardContent className="flex min-h-40 flex-col items-center justify-center text-center">
                  <KeyRound className="mb-3 h-8 w-8 text-muted-foreground" />
                  <p className="font-medium">还没有个人模型凭据</p>
                  <p className="mt-1 text-sm text-muted-foreground">
                    Agent 目前会使用明确配置的 Operator default，不会伪装成个人连接。
                  </p>
                </CardContent>
              </Card>
            ) : (
              credentials.map((credential) => (
                <CredentialCard
                  key={credential.id}
                  credential={credential}
                  busy={busyCredential === credential.id}
                  onAction={(action) => runCredentialAction(credential, action)}
                />
              ))
            )}
          </div>
        </TabsContent>

        <TabsContent value="runtime" className="grid gap-5 lg:grid-cols-[1fr_360px]">
          <Card className="border-border/70">
            <CardHeader>
              <CardTitle>当前 Agent 模型来源</CardTitle>
              <CardDescription>
                每次调用都会按当前用户重新解析，不把个人密钥写入全局单例。
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-5">
              <div className="rounded-2xl border border-border/70 bg-gradient-to-br from-primary/10 via-background to-background p-5">
                <div className="flex items-start justify-between gap-4">
                  <div>
                    <p className="text-sm text-muted-foreground">Credential source</p>
                    <p className="mt-1 text-xl font-semibold">
                      {runtime?.credential_source === 'personal'
                        ? 'Personal credential'
                        : runtime?.credential_source === 'operator_default'
                          ? 'Operator default'
                          : 'Unavailable'}
                    </p>
                    <p className="mt-2 font-mono text-sm text-muted-foreground">
                      {runtime?.provider_id ?? '—'} / {runtime?.model_id ?? '—'}
                    </p>
                  </div>
                  <Badge
                    variant={
                      runtime?.credential_source === 'unavailable' ? 'destructive' : 'success'
                    }
                  >
                    {runtime?.credential_source === 'unavailable' ? 'OFFLINE' : 'READY'}
                  </Badge>
                </div>
              </div>
              <div className="grid gap-3 sm:grid-cols-2">
                <Posture title="单 Agent Alpha" status="可用" good />
                <Posture
                  title="个人 Provider"
                  status={activeCredential ? '已选择' : '未选择'}
                  good={!!activeCredential}
                />
                <Posture title="工具 / MCP / Skills" status="关闭" />
                <Posture title="Planner / 多 Agent" status="关闭" />
              </div>
            </CardContent>
          </Card>

          <Card className="border-border/70 bg-muted/20">
            <CardHeader>
              <CardTitle className="text-base">连接规则</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3 text-sm text-muted-foreground">
              <p>• Personal credential 必须先通过严格模型身份测试。</p>
              <p>• 未配置个人凭据时，才使用明确标识的 Operator default。</p>
              <p>• 不允许静默替换 Model ID，也不跟随 Provider 重定向。</p>
              <p>• 工具、Shell、SQL、任意 HTTP、多 Agent 仍保持关闭。</p>
              <div className="pt-2">
                <Badge variant={operatorFallback ? 'secondary' : 'outline'}>
                  Operator fallback {operatorFallback ? 'available' : 'unavailable'}
                </Badge>
              </div>
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>
    </div>
  )
}

function Field({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between gap-3">
        <Label>{label}</Label>
        {hint && <span className="text-xs text-muted-foreground">{hint}</span>}
      </div>
      {children}
    </div>
  )
}

function InfoRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between gap-4 border-b border-border/60 pb-3 last:border-0">
      <span className="text-muted-foreground">{label}</span>
      <span className="max-w-[210px] truncate font-medium">{value}</span>
    </div>
  )
}

function CredentialCard({
  credential,
  busy,
  onAction,
}: {
  credential: ProviderCredentialRead
  busy: boolean
  onAction: (action: 'test' | 'activate' | 'revoke') => void
}) {
  return (
    <Card className="border-border/70">
      <CardContent className="flex flex-col gap-5 p-5 lg:flex-row lg:items-center lg:justify-between">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="font-semibold">{credential.display_name}</h3>
            {credential.is_default && credential.is_active && (
              <Badge variant="success">DEFAULT</Badge>
            )}
            {!credential.is_active && <Badge variant="secondary">REVOKED</Badge>}
            {credential.last_test_status && (
              <Badge variant={credential.last_test_status === 'passed' ? 'success' : 'destructive'}>
                {credential.last_test_status}
              </Badge>
            )}
          </div>
          <p className="mt-2 truncate font-mono text-sm text-muted-foreground">
            {credential.provider_id} / {credential.model_id}
          </p>
          <p className="mt-1 truncate text-xs text-muted-foreground">{credential.base_url}</p>
          <div className="mt-3 flex flex-wrap gap-4 text-xs text-muted-foreground">
            <span>Key {credential.key_fingerprint ?? 'not configured'}</span>
            <span>
              {credential.last_test_latency_ms == null
                ? 'Not tested'
                : `${credential.last_test_latency_ms} ms`}
            </span>
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          {credential.is_active && (
            <Button variant="outline" onClick={() => onAction('test')} disabled={busy}>
              {busy ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <RotateCw className="h-4 w-4" />
              )}
              测试连接
            </Button>
          )}
          {credential.is_active && !credential.is_default && (
            <Button variant="secondary" onClick={() => onAction('activate')} disabled={busy}>
              <CheckCircle2 className="h-4 w-4" /> 设为默认
            </Button>
          )}
          {credential.is_active && (
            <Button variant="ghost" onClick={() => onAction('revoke')} disabled={busy}>
              <Trash2 className="h-4 w-4" /> 撤销
            </Button>
          )}
        </div>
      </CardContent>
    </Card>
  )
}

function Posture({
  title,
  status,
  good = false,
}: {
  title: string
  status: string
  good?: boolean
}) {
  return (
    <div className="rounded-xl border border-border/70 p-4">
      <p className="text-sm font-medium">{title}</p>
      <p className={`mt-1 text-xs ${good ? 'text-emerald-600' : 'text-muted-foreground'}`}>
        {status}
      </p>
    </div>
  )
}
