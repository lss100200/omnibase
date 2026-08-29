'use client'

import {
  Archive,
  ChevronDown,
  ChevronRight,
  FileCode2,
  Folder,
  FolderOpen,
  LoaderCircle,
  Plus,
  Unplug,
} from 'lucide-react'
import {
  p7ActivityLabel,
  projectP7Blackboard,
  projectP7RunRows,
  projectP7ThreadRows,
  type P7Activity,
} from '@/lib/p7-workbench-shell'
import { projectDesktopTeamBudget } from '@/lib/desktop-team-surface'
import { p7WorkspaceFileDirectory, p7WorkspaceFileErrorMessage } from '@/lib/p7-workspace-files'
import type { P7WorkbenchProps } from './p7-shell'
import { P7SidebarComponentSurface } from './p7-component-surface'

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
  const fileState = props.workspaceFiles
  const authorization = fileState.authorization
  const fileError = p7WorkspaceFileErrorMessage(fileState.errorCode)

  function renderDirectory(directoryPath: string, depth: number): React.ReactNode {
    const directory = p7WorkspaceFileDirectory(fileState, directoryPath)
    if (directory === null) return null
    return (
      <>
        {directory.entries.map((entry) => {
          const expanded = fileState.expandedDirectoryPaths.includes(entry.path)
          const selected = fileState.selectedPath === entry.path
          return (
            <div key={entry.path}>
              <button
                type="button"
                className={`p7-file-entry${selected ? 'p7-active' : ''}`}
                style={{ paddingLeft: `${12 + depth * 13}px` }}
                onClick={() => {
                  if (entry.kind === 'directory') {
                    props.onToggleWorkspaceDirectory(entry.path, !expanded)
                  } else {
                    props.onOpenWorkspaceFile(entry.path)
                  }
                }}
              >
                {entry.kind === 'directory' ? (
                  expanded ? (
                    <ChevronDown size={12} />
                  ) : (
                    <ChevronRight size={12} />
                  )
                ) : (
                  <span className="p7-file-spacer" />
                )}
                {entry.kind === 'directory' ? (
                  expanded ? (
                    <FolderOpen size={14} />
                  ) : (
                    <Folder size={14} />
                  )
                ) : (
                  <FileCode2 size={14} />
                )}
                <span className="p7-row-text">{entry.name}</span>
                {entry.kind === 'file' && entry.sizeBytes !== null && (
                  <span className="p7-row-meta">{entry.sizeBytes.toLocaleString()} B</span>
                )}
              </button>
              {entry.kind === 'directory' && expanded && (
                <>
                  {p7WorkspaceFileDirectory(fileState, entry.path)?.status === 'loading' && (
                    <div
                      className="p7-file-loading"
                      style={{ paddingLeft: `${38 + depth * 13}px` }}
                    >
                      正在读取…
                    </div>
                  )}
                  {p7WorkspaceFileDirectory(fileState, entry.path)?.status === 'error' && (
                    <div
                      className="p7-file-loading"
                      style={{ paddingLeft: `${38 + depth * 13}px` }}
                    >
                      {p7WorkspaceFileErrorMessage(
                        p7WorkspaceFileDirectory(fileState, entry.path)?.errorCode ?? null,
                      )}
                    </div>
                  )}
                  {renderDirectory(entry.path, depth + 1)}
                </>
              )}
            </div>
          )
        })}
        {directory.truncated && (
          <div className="p7-file-loading" style={{ paddingLeft: `${25 + depth * 13}px` }}>
            此目录仅显示前 500 项。
          </div>
        )}
      </>
    )
  }

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
      <P7SectionLabel>本地文件</P7SectionLabel>
      {authorization === null ? (
        <div className="p7-file-empty">
          <button
            type="button"
            className="p7-text-button"
            title="选择本地文件夹（只读）"
            disabled={props.workspaceId === null || fileState.phase === 'authorizing'}
            onClick={props.onAuthorizeWorkspaceFiles}
          >
            {fileState.phase === 'authorizing' ? (
              <LoaderCircle className="p7-spin" size={13} />
            ) : (
              <FolderOpen size={13} />
            )}
            {fileState.phase === 'authorizing' ? '正在选择…' : '选择文件夹'}
          </button>
          <div>未打开文件夹</div>
          {fileError !== null && fileState.phase === 'error' && (
            <div className="p7-file-error">{fileError}</div>
          )}
        </div>
      ) : (
        <div className="p7-file-tree" aria-label={`本地文件：${authorization.rootName}`}>
          <div className="p7-file-root">
            <button
              type="button"
              className="p7-file-entry"
              onClick={() =>
                props.onToggleWorkspaceDirectory('', !fileState.expandedDirectoryPaths.includes(''))
              }
            >
              {fileState.expandedDirectoryPaths.includes('') ? (
                <ChevronDown size={12} />
              ) : (
                <ChevronRight size={12} />
              )}
              <FolderOpen size={14} />
              <span className="p7-row-text">{authorization.rootName}</span>
            </button>
            <button
              type="button"
              className="p7-file-release"
              aria-label="释放本地目录授权"
              title="释放目录授权"
              onClick={props.onReleaseWorkspaceFiles}
            >
              <Unplug size={13} />
            </button>
          </div>
          {fileState.expandedDirectoryPaths.includes('') && (
            <>
              {p7WorkspaceFileDirectory(fileState, '')?.status === 'loading' && (
                <div className="p7-file-loading">正在读取目录…</div>
              )}
              {p7WorkspaceFileDirectory(fileState, '')?.status === 'error' && (
                <div className="p7-file-loading">
                  {p7WorkspaceFileErrorMessage(
                    p7WorkspaceFileDirectory(fileState, '')?.errorCode ?? null,
                  )}
                </div>
              )}
              {renderDirectory('', 0)}
            </>
          )}
        </div>
      )}
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
      </div>
      <P7SidebarComponentSurface projection={props.componentSurface} />
    </aside>
  )
}
