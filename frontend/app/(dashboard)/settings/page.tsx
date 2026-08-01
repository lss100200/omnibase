'use client'

import { useAuth } from '@/lib/hooks/use-auth'
import { Badge } from '@/components/ui/badge'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'

/**
 * Settings page (Phase 0: read-only info).
 *
 * Phase 2 will add:
 * - Profile editing (name, avatar)
 * - Tenant management
 * - API token management
 * - MCP / Skill configuration
 */
export default function SettingsPage() {
  const { user, tenant } = useAuth()

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">设置</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          查看你的账号与工作空间信息（Phase 0：只读）
        </p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-lg">账号</CardTitle>
          <CardDescription>你的登录信息</CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <Row label="邮箱" value={user?.email || '—'} />
          <Row
            label="管理员"
            value={
              user?.is_tenant_admin ? (
                <Badge variant="success">是</Badge>
              ) : (
                <Badge variant="secondary">否</Badge>
              )
            }
          />
          <Row label="注册时间" value={user?.created_at || '—'} />
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-lg">工作空间</CardTitle>
          <CardDescription>当前数据隔离边界</CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <Row label="名称" value={tenant?.name || '—'} />
          <Row label="Slug" value={<code className="text-sm">{tenant?.slug || '—'}</code>} />
          <Row label="Schema" value={<code className="text-sm">tenant_***（已隐藏）</code>} />
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-lg">即将到来</CardTitle>
          <CardDescription>Phase 2+ 计划的功能</CardDescription>
        </CardHeader>
        <CardContent className="space-y-2 text-sm text-muted-foreground">
          <p>• 个人资料编辑（头像、显示名）</p>
          <p>• 工作空间成员邀请</p>
          <p>• API Token 管理</p>
          <p>• Skill 与 MCP 配置面板</p>
          <p>• 数据导出 / 备份</p>
        </CardContent>
      </Card>
    </div>
  )
}

function Row({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-4">
      <span className="text-sm text-muted-foreground">{label}</span>
      <span className="text-sm font-medium">{value}</span>
    </div>
  )
}
