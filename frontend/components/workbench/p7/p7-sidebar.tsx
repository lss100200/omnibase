'use client'

import { Archive, Plus } from 'lucide-react'
import {
  p7ActivityLabel,
  projectP7Blackboard,
  projectP7RunRows,
  projectP7ThreadRows,
  type P7Activity,
} from '@/lib/p7-workbench-shell'
import { projectDesktopTeamBudget } from '@/lib/desktop-team-surface'
import type { P7WorkbenchProps } from './p7-shell'

export const P7_TEAM_SPECIALISTS = [
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

function P7SectionLabel({ children }: { readonly children: string }) {
  return <div className="p7-sidebar-section-label">{children}</div>
}

function P7UnavailablePanel({ title }: { readonly title: string }) {
  return (
    <div>
      <P7SectionLabel>{title}</P7SectionLabel>
      <div className="p7-sidebar-note">该功能没有可信的数据源；本界面不会显示任何模拟内容。</div>
    </div>
  )
}

function P7ExplorerPanel(props: P7WorkbenchProps) {
  const activeWorkspaces = props.workspaces.filter((item) => item.state === 'active')
  const activeConversations = props.conversations.filter((item) => item.state === 'active')
  return (
    <div>
      <P7SectionLabel>工作空间</P7SectionLabel>
      <form
        className="p7-sidebar-form"
        onSubmit={(event) => {
          event.preventDefault()
          const name = props.workspaceNameInput.trim()
          if (name === '') return
          props.onCreateWorkspace(name)
        }}
      >
        <input
          className="p7-sidebar-input"
          value={props.workspaceNameInput}
          onChange={(event) => props.onWorkspaceNameInputChange(event.target.value)}
          placeholder="新工作空间"
          aria-label="新工作空间名称"
        />
        <button
          type="submit"
          className="p7-text-button"
          aria-label="创建工作空间"
          disabled={props.workspaceNameInput.trim() === ''}
        >
          <Plus size={14} />
        </button>
      </form>
      {activeWorkspaces.length === 0 && (
        <div className="p7-sidebar-note">还没有工作空间；创建第一个工作空间后开始。</div>
      )}
      {activeWorkspaces.map((workspace) => (
        <button
          key={workspace.id}
          type="button"
          className={`p7-row${workspace.id === props.workspaceId ? 'p7-active' : ''}`}
          onClick={() => props.onSelectWorkspace(workspace.id)}
        >
          <span className="p7-row-text">{workspace.name}</span>
          {workspace.state === 'archived' && <span className="p7-row-meta">已归档</span>}
        </button>
      ))}
      <P7SectionLabel>会话</P7SectionLabel>
      <button
        type="button"
        className="p7-row"
        disabled={props.workspaceId === null}
        onClick={() => props.onCreateConversation()}
      >
        <Plus size={14} />
        <span className="p7-row-text">新建会话</span>
      </button>
      {activeConversations.length === 0 && <div className="p7-sidebar-note">还没有会话。</div>}
      {activeConversations.map((conversation) => (
        <div
          key={conversation.id}
          className={`p7-row${conversation.id === props.conversationId ? 'p7-active' : ''}`}
        >
          <button
            type="button"
            className="p7-row"
            style={{ padding: '3px 0', flex: '1', minWidth: 0 }}
            onClick={() => props.onSelectConversation(conversation.id)}
          >
            <span className="p7-row-text">{conversation.title}</span>
            <span className="p7-row-meta">
              {new Date(conversation.updatedAt).toLocaleTimeString('zh-CN', {
                hour: '2-digit',
                minute: '2-digit',
              })}
            </span>
          </button>
          <button
            type="button"
            className="p7-icon-button"
            style={{ width: '24px', height: '24px', flex: 'none' }}
            aria-label={`归档会话：${conversation.title}`}
            title="归档会话"
            onClick={() => props.onArchiveConversation(conversation.id)}
          >
            <Archive size={13} />
          </button>
        </div>
      ))}
    </div>
  )
}

function P7RunPanel(props: P7WorkbenchProps) {
  const budgetText = projectDesktopTeamBudget(props.teamLive)
  return (
    <div>
      <P7SectionLabel>运行配置</P7SectionLabel>
      <label className="p7-check-row">
        <input
          type="checkbox"
          checked={props.teamMode}
          onChange={(event) => props.onTeamModeChange(event.target.checked)}
        />
        团队协作（Owner 任务级委托：父 Agent 判断编制，宿主校验后执行）
      </label>
      {props.teamMode && (
        <div className="p7-sidebar-note">
          <div>{budgetText}</div>
          {props.teamAppendBudgetTarget !== null && (
            <div className="p7-sidebar-form" style={{ padding: '6px 0 0' }}>
              <input
                className="p7-sidebar-input"
                value={props.appendCalls}
                onChange={(event) => props.onAppendCallsChange(event.target.value)}
                aria-label="追加调用预算"
              />
              <button
                type="button"
                className="p7-text-button"
                disabled={props.teamAppendBudgetTarget === null}
                onClick={() => {
                  const next = Number.parseInt(props.appendCalls, 10)
                  if (!Number.isInteger(next)) return
                  props.onAppendBudget(next)
                }}
              >
                追加预算
              </button>
            </div>
          )}
          <details>
            <summary style={{ cursor: 'pointer', color: 'var(--p7-muted)' }}>
              允许父 Agent 使用的员工（默认全部允许，非每次任务编制）
            </summary>
            <div className="p7-sidebar-note" style={{ margin: '6px 0 0' }}>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '2px 8px' }}>
                {P7_TEAM_SPECIALISTS.map((role) => (
                  <label key={role} className="p7-check-row" style={{ padding: '1px 0' }}>
                    <input
                      type="checkbox"
                      checked={props.allowedSpecialists.includes(role)}
                      onChange={(event) => {
                        props.onAllowedSpecialistsChange(
                          event.target.checked
                            ? [...props.allowedSpecialists, role]
                            : props.allowedSpecialists.filter((item) => item !== role),
                        )
                      }}
                    />
                    {role}
                  </label>
                ))}
              </div>
            </div>
          </details>
        </div>
      )}
      <P7SectionLabel>运行历史</P7SectionLabel>
      {props.runHistoryStatus === 'loading' && (
        <div className="p7-sidebar-note">正在加载运行历史…</div>
      )}
      {props.runHistoryStatus === 'error' && (
        <div className="p7-sidebar-note">运行历史加载失败。</div>
      )}
      {props.runHistoryStatus === 'ready' && props.runHistory.length === 0 && (
        <div className="p7-sidebar-note">还没有团队运行记录。</div>
      )}
      {props.runHistoryStatus === 'ready' &&
        projectP7RunRows(props.runHistory, props.selectedRunId).map((row) => (
          <button
            key={row.run.id}
            type="button"
            className={`p7-row${row.active ? 'p7-active' : ''}`}
            onClick={() => props.onSelectRun(row.run.id)}
          >
            <span className="p7-row-stack">
              <span className="p7-row-text">{row.stateLabel}</span>
              <span className="p7-row-sub">{row.run.task}</span>
            </span>
            {row.meta !== null && <span className="p7-row-meta">{row.meta}</span>}
          </button>
        ))}
    </div>
  )
}

function P7AgentsPanel(props: P7WorkbenchProps) {
  const rows = projectP7ThreadRows({
    conversations: props.conversations,
    selectedConversationId: props.conversationId,
    teamPhase: props.teamLive.phase,
    teamOriginConversationId: props.teamLive.originConversationId,
    live: props.live,
  })
  return (
    <div>
      <P7SectionLabel>活动线程</P7SectionLabel>
      {rows.length === 0 && <div className="p7-sidebar-note">还没有会话。</div>}
      {rows.map((row) => (
        <button
          key={row.conversationId}
          type="button"
          className={`p7-row${row.active ? 'p7-active' : ''}`}
          onClick={() => props.onSelectConversation(row.conversationId)}
        >
          <span className={`p7-dot p7-dot-${row.dotTone}`} />
          <span className="p7-row-stack">
            <span className="p7-row-text">{row.title}</span>
            <span className="p7-row-sub">{row.statusText}</span>
          </span>
        </button>
      ))}
    </div>
  )
}

function P7BlackboardPanel(props: P7WorkbenchProps) {
  const section: ReturnType<typeof projectP7Blackboard> | null =
    props.blackboard === null ? null : projectP7Blackboard(props.blackboard)
  return (
    <div>
      <P7SectionLabel>任务简报</P7SectionLabel>
      {props.blackboardStatus === 'loading' && <div className="p7-sidebar-note">正在读取黑板…</div>}
      {props.blackboardStatus === 'error' && <div className="p7-sidebar-note">黑板读取失败。</div>}
      {section === null && props.blackboardStatus !== 'loading' && (
        <div className="p7-sidebar-note">
          还没有可显示的任务简报；启动一次团队协作后，黑板内容会显示在这里。
        </div>
      )}
      {section !== null && (
        <div className="p7-sidebar-note" style={{ borderLeftColor: 'var(--p7-accent)' }}>
          <div className="p7-row-sub" style={{ marginBottom: '6px' }}>
            目标：{section.ownerObjective}
          </div>
          {section.currentPlanRevisionId !== null && (
            <div className="p7-row-sub">计划修订：{section.currentPlanRevisionId}</div>
          )}
          {section.assignments.length > 0 && (
            <div className="p7-row-sub" style={{ marginTop: '6px' }}>
              分配：
              {section.assignments.map((assignment) => (
                <div key={assignment.assignmentId}>
                  · {assignment.roleLabel}（{assignment.stateLabel}
                  {assignment.waveId !== null ? ` · ${assignment.waveId}` : ''}）
                </div>
              ))}
            </div>
          )}
          {section.collaborationRequests.length > 0 && (
            <div className="p7-row-sub" style={{ marginTop: '6px' }}>
              协作请求：
              {section.collaborationRequests.map((request) => (
                <div key={request.id ?? request.question}>
                  · {request.fromRoleLabel} → {request.targetRoleLabel}（{request.decisionLabel}）
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

function P7SettingsPanel(props: P7WorkbenchProps) {
  return (
    <div style={{ paddingBottom: '16px' }}>
      <P7SectionLabel>模型 Provider</P7SectionLabel>
      <form
        className="p7-sidebar-form"
        style={{ flexDirection: 'column', alignItems: 'stretch', gap: '8px' }}
        onSubmit={(event) => {
          event.preventDefault()
          props.onSaveProvider()
        }}
      >
        <input
          className="p7-sidebar-input"
          value={props.providerForm.displayName}
          onChange={(event) => props.onProviderFormChange({ displayName: event.target.value })}
          placeholder="显示名称"
          aria-label="Provider 显示名称"
        />
        <input
          className="p7-sidebar-input"
          value={props.providerForm.baseUrl}
          onChange={(event) => props.onProviderFormChange({ baseUrl: event.target.value })}
          placeholder="https://api.deepseek.com/v1"
          aria-label="Base URL"
        />
        <input
          className="p7-sidebar-input"
          type="password"
          value={props.providerForm.apiKey}
          onChange={(event) => props.onProviderFormChange({ apiKey: event.target.value })}
          autoComplete="off"
          placeholder="API Key（不回读）"
          aria-label="API Key"
        />
        <input
          className="p7-sidebar-input"
          value={props.providerForm.modelName}
          onChange={(event) => props.onProviderFormChange({ modelName: event.target.value })}
          placeholder="deepseek-chat"
          aria-label="模型名称"
        />
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '6px' }}>
          <select
            className="p7-sidebar-input"
            value={props.providerForm.gear}
            onChange={(event) =>
              props.onProviderFormChange({
                gear: event.target.value as typeof props.providerForm.gear,
              })
            }
            aria-label="档位"
          >
            <option value="economy">经济</option>
            <option value="standard">标准</option>
            <option value="deep">深度</option>
            <option value="audit">审计</option>
          </select>
          <select
            className="p7-sidebar-input"
            value={props.providerForm.thinkingDepth}
            onChange={(event) =>
              props.onProviderFormChange({
                thinkingDepth: event.target.value as typeof props.providerForm.thinkingDepth,
              })
            }
            aria-label="思考深度"
          >
            <option value="disabled">关闭</option>
            <option value="low">低</option>
            <option value="medium">中</option>
            <option value="high">高</option>
          </select>
        </div>
        <label className="p7-check-row" style={{ padding: '0' }}>
          <input
            type="checkbox"
            checked={props.providerForm.allowLoopbackHttp}
            onChange={(event) =>
              props.onProviderFormChange({ allowLoopbackHttp: event.target.checked })
            }
          />
          允许本机 HTTP（127.0.0.1 / localhost）
        </label>
        <button type="submit" className="p7-text-button" disabled={props.submitting}>
          {props.submitting ? '保存中…' : '保存 Provider'}
        </button>
      </form>
      {props.testResult !== null && <div className="p7-sidebar-note">{props.testResult}</div>}
      <P7SectionLabel>已保存 Provider</P7SectionLabel>
      {props.providers.length === 0 && <div className="p7-sidebar-note">还没有 Provider。</div>}
      {props.providers.map((provider) => (
        <div
          key={provider.id}
          className="p7-sidebar-note"
          style={{ borderLeftColor: 'var(--p7-accent)' }}
        >
          <div className="p7-row-text" style={{ color: 'var(--p7-text)' }}>
            {provider.displayName}
          </div>
          <div className="p7-row-sub">{provider.modelName}</div>
          <button
            type="button"
            className="p7-text-button"
            style={{ marginTop: '6px' }}
            onClick={() => props.onTestProvider(provider.id)}
          >
            测试
          </button>
        </div>
      ))}
    </div>
  )
}

export function P7Sidebar({
  activity,
  ...props
}: { readonly activity: P7Activity } & P7WorkbenchProps) {
  return (
    <aside className="p7-sidebar" aria-label={p7ActivityLabel(activity)}>
      <div className="p7-sidebar-title">
        <span>{p7ActivityLabel(activity)}</span>
      </div>
      <div className="p7-sidebar-content">
        {activity === 'explorer' && <P7ExplorerPanel {...props} />}
        {activity === 'search' && <P7UnavailablePanel title="搜索" />}
        {activity === 'source' && <P7UnavailablePanel title="源代码管理" />}
        {activity === 'run' && <P7RunPanel {...props} />}
        {activity === 'agents' && <P7AgentsPanel {...props} />}
        {activity === 'blackboard' && <P7BlackboardPanel {...props} />}
        {activity === 'settings' && <P7SettingsPanel {...props} />}
      </div>
    </aside>
  )
}
