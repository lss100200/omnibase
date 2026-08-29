'use client'

import {
  Boxes,
  ChevronDown,
  ChevronUp,
  FileCode2,
  GitCompareArrows,
  MessageSquare,
  NotebookPen,
  Settings,
  type LucideIcon,
} from 'lucide-react'
import type {
  DesktopInvocationLiveProjection,
  DesktopMessage,
  PersonalTeamBlackboard,
} from '@/lib/desktop-bridge'
import {
  p7BottomTabAvailability,
  p7BottomTabLabel,
  p7CenterViewLabel,
  p7ViewAvailability,
  projectP7Blackboard,
  selectP7BottomTab,
  setP7BottomOpen,
  setP7CenterView,
  type P7BottomTab,
  type P7CenterView,
  type P7DataSourcePresence,
  type P7ShellUiState,
} from '@/lib/p7-workbench-shell'
import type { P7WorkspaceComponentSurfaceProjection } from '@/lib/p7-workspace-components'
import { p7WorkspaceFileErrorMessage, type P7WorkspaceFilesState } from '@/lib/p7-workspace-files'
import { P7ComponentSurface } from './p7-component-surface'
import { P7SettingsCenter, type P7SettingsCenterProps } from './p7-settings-center'

function invocationStatusLabel(status: string): string {
  switch (status) {
    case 'succeeded':
      return '已完成'
    case 'failed':
      return '失败'
    case 'cancelled':
      return '调用已取消'
    case 'unknown':
      return '调用状态未知'
    case 'running':
    case 'streaming':
      return '生成中'
    default:
      return status
  }
}

function P7UnavailableView({ title }: { readonly title: string }) {
  return (
    <div className="p7-unavailable" role="status">
      <div className="p7-unavailable-title">{title}</div>
      <div className="p7-unavailable-reason">
        该功能没有可信的数据源；本界面不会显示任何模拟内容。接入真实的数据源后，此视图会解除不可用状态。
      </div>
    </div>
  )
}

function P7TranscriptView({
  messages,
  messagesStatus,
  messagesError,
  agentName,
  teamProjection,
  liveProjection,
  stopping,
}: {
  readonly messages: readonly DesktopMessage[]
  readonly messagesStatus: 'empty' | 'loading' | 'ready' | 'error'
  readonly messagesError: string | null
  readonly agentName: string
  readonly teamProjection: {
    readonly visible: boolean
    readonly parentLiveText: string
    readonly parentFinalAnswer: string | null
  }
  readonly liveProjection: DesktopInvocationLiveProjection
  readonly stopping: boolean
}) {
  return (
    <div className="p7-transcript" aria-live="polite">
      {messagesStatus === 'loading' && messages.length === 0 && (
        <div className="p7-transcript-empty">正在加载会话记录…</div>
      )}
      {messagesStatus === 'error' && messagesError !== null && (
        <div className="p7-transcript-empty">{messagesError}</div>
      )}
      {messagesStatus === 'ready' && messages.length === 0 && (
        <div className="p7-transcript-empty">
          还没有消息。在右侧 Agent 面板输入第一条指令，或切换团队协作模式运行任务。
        </div>
      )}
      {messages.map((message) => (
        <div key={message.id} className="p7-message">
          <div className="p7-message-avatar">{message.role === 'user' ? '你' : 'A'}</div>
          <div className="p7-message-body">
            <div className="p7-message-head">
              <span className="p7-message-role">{message.role === 'user' ? '你' : agentName}</span>
              {message.retryOfMessageId && (
                <span className="p7-message-state">重试自前一次调用</span>
              )}
              {message.invocation && (
                <span
                  className={`p7-message-state${
                    message.invocation.status === 'failed'
                      ? 'p7-state-error'
                      : message.invocation.status === 'succeeded'
                        ? 'p7-state-ok'
                        : ''
                  }`}
                >
                  {invocationStatusLabel(message.invocation.status)}
                  {message.invocation.retryOfInvocationId ? ' · 新调用' : ''}
                  {message.invocation.errorRedacted ? ` · ${message.invocation.errorRedacted}` : ''}
                </span>
              )}
            </div>
            <div className="p7-message-content">
              {message.content || (message.status === 'cancelled' ? '生成已停止' : '')}
            </div>
          </div>
        </div>
      ))}
      {teamProjection.visible && teamProjection.parentFinalAnswer !== null && (
        <div className="p7-team-answer">
          父 Agent 最终回答
          {`\n`}
          {teamProjection.parentFinalAnswer}
        </div>
      )}
      {teamProjection.visible &&
        teamProjection.parentLiveText !== '' &&
        teamProjection.parentFinalAnswer === null && (
          <div className="p7-message-content">{teamProjection.parentLiveText}</div>
        )}
      {stopping && liveProjection.visible && liveProjection.liveText === '' && (
        <div className="p7-transcript-empty">正在停止</div>
      )}
      {liveProjection.visible && liveProjection.liveMeta && (
        <details className="p7-message-state" style={{ margin: '4px 0 14px' }}>
          <summary style={{ cursor: 'pointer', color: 'var(--p7-faint)' }}>调用详情</summary>
          <div className="p7-brief-row">
            <span className="p7-brief-row-label">请求模型</span>
            <span className="p7-brief-row-value">
              {liveProjection.liveMeta.requestedModel ?? '—'}
            </span>
          </div>
          <div className="p7-brief-row">
            <span className="p7-brief-row-label">实际模型</span>
            <span className="p7-brief-row-value">{liveProjection.liveMeta.actualModel ?? '—'}</span>
          </div>
          <div className="p7-brief-row">
            <span className="p7-brief-row-label">Provider</span>
            <span className="p7-brief-row-value">
              {liveProjection.liveMeta.providerName ?? '—'}
            </span>
          </div>
          <div className="p7-brief-row">
            <span className="p7-brief-row-label">状态</span>
            <span className="p7-brief-row-value">
              {invocationStatusLabel(liveProjection.liveMeta.status ?? 'running')}
            </span>
          </div>
          <div className="p7-brief-row">
            <span className="p7-brief-row-label">耗时</span>
            <span className="p7-brief-row-value">
              {liveProjection.liveMeta.durationMs ?? '—'} ms
            </span>
          </div>
          <div className="p7-brief-row">
            <span className="p7-brief-row-label">Tokens</span>
            <span className="p7-brief-row-value">
              {liveProjection.liveMeta.totalTokens ?? '未提供'}
            </span>
          </div>
          <div className="p7-brief-row">
            <span className="p7-brief-row-label">思考深度</span>
            <span className="p7-brief-row-value">
              {liveProjection.liveMeta.thinkingDepth ?? '—'}
            </span>
          </div>
          {liveProjection.liveMeta.errorRedacted && (
            <div className="p7-brief-row">
              <span className="p7-brief-row-label">错误</span>
              <span className="p7-brief-row-value">{liveProjection.liveMeta.errorRedacted}</span>
            </div>
          )}
        </details>
      )}
    </div>
  )
}

function P7BriefView({
  blackboard,
  blackboardStatus,
}: {
  readonly blackboard: PersonalTeamBlackboard | null
  readonly blackboardStatus: 'idle' | 'loading' | 'ready' | 'error'
}) {
  const section = blackboard === null ? null : projectP7Blackboard(blackboard)
  return (
    <div className="p7-brief">
      {blackboardStatus === 'loading' && <div className="p7-brief-empty">正在读取黑板…</div>}
      {blackboardStatus === 'error' && <div className="p7-brief-empty">黑板读取失败。</div>}
      {section === null && blackboardStatus !== 'loading' && (
        <div className="p7-brief-empty">
          还没有可显示的任务简报。启动一次团队协作后，黑板内容会显示在这里。
        </div>
      )}
      {section !== null && (
        <>
          <h2>任务简报</h2>
          <div className="p7-brief-meta">
            {section.teamRunId} · 计划修订 {section.currentPlanRevisionId ?? '—'}
          </div>
          <h3>目标</h3>
          <div className="p7-brief-row">
            <span className="p7-brief-row-label">Owner 目标</span>
            <span className="p7-brief-row-value">{section.ownerObjective}</span>
          </div>
          <h3>分配</h3>
          {section.assignments.length === 0 && <div className="p7-muted-text">尚无分配。</div>}
          {section.assignments.map((assignment) => (
            <div key={assignment.assignmentId} className="p7-brief-row">
              <span className="p7-brief-row-label">
                {assignment.roleLabel} · {assignment.stateLabel}
              </span>
              <span className="p7-brief-row-value">
                {assignment.waveId !== null ? `${assignment.waveId} · ` : ''}
                {assignment.objective}
              </span>
            </div>
          ))}
          <h3>报告</h3>
          {section.reports.length === 0 && <div className="p7-muted-text">尚无报告。</div>}
          {section.reports.map((report) => (
            <div key={report.assignmentId} className="p7-brief-row">
              <span className="p7-brief-row-label">
                {report.roleLabel} · {report.status}
              </span>
              <span className="p7-brief-row-value">{report.report}</span>
            </div>
          ))}
          <h3>协作请求</h3>
          {section.collaborationRequests.length === 0 && (
            <div className="p7-muted-text">无待处理协作。</div>
          )}
          {section.collaborationRequests.map((request) => (
            <div key={request.id ?? request.question} className="p7-brief-row">
              <span className="p7-brief-row-label">
                {request.fromRoleLabel} → {request.targetRoleLabel}
              </span>
              <span className="p7-brief-row-value">
                {request.question}
                {`\n`}
                <span className="p7-row-sub">{request.decisionLabel}</span>
              </span>
            </div>
          ))}
        </>
      )}
    </div>
  )
}

function P7CodeView({ files }: { readonly files: P7WorkspaceFilesState }) {
  const error = p7WorkspaceFileErrorMessage(files.errorCode)
  if (files.readPhase === 'loading') {
    return (
      <div className="p7-code-empty" role="status">
        <FileCode2 size={18} />
        <span>正在读取 {files.selectedPath ?? '文件'}…</span>
      </div>
    )
  }
  if (files.readPhase === 'error') {
    return (
      <div className="p7-code-empty" role="alert">
        <FileCode2 size={18} />
        <strong>无法打开 {files.selectedPath ?? '文件'}</strong>
        <span>{error ?? '本机文件操作未完成。'}</span>
      </div>
    )
  }
  if (files.openFile === null) {
    return (
      <div className="p7-code-empty" role="status">
        <FileCode2 size={18} />
        <span>未打开文件</span>
      </div>
    )
  }
  return (
    <div className="p7-code-view">
      <div className="p7-code-head">
        <span className="p7-code-path">{files.openFile.path}</span>
        <span className="p7-code-meta">
          {files.openFile.sizeBytes.toLocaleString()} B · SHA-256{' '}
          {files.openFile.sha256.slice(0, 12)} · 只读
        </span>
      </div>
      <pre className="p7-code-content" tabIndex={0} aria-label={`只读代码：${files.openFile.path}`}>
        <code>{files.openFile.content}</code>
      </pre>
    </div>
  )
}

function P7BottomPanel({
  ui,
  onUiChange,
  presence,
  eventLog,
  outputLines,
}: {
  readonly ui: P7ShellUiState
  readonly onUiChange: (next: P7ShellUiState) => void
  readonly presence: P7DataSourcePresence
  readonly eventLog: readonly string[]
  readonly outputLines: readonly string[]
}) {
  const tabs: readonly P7BottomTab[] = ['terminal', 'problems', 'output', 'agent-log']
  return (
    <div className="p7-bottom-panel">
      <div className="p7-bottom-tabbar" role="tablist" aria-label="底部面板">
        {tabs.map((tab) => {
          const availability = p7BottomTabAvailability(tab, presence)
          return (
            <button
              key={tab}
              type="button"
              role="tab"
              className="p7-bottom-tab"
              aria-selected={ui.bottomTab === tab}
              aria-disabled={!availability.available && tab !== 'agent-log' && tab !== 'output'}
              title={availability.available ? undefined : (availability.reason ?? undefined)}
              onClick={() => onUiChange(selectP7BottomTab(ui, tab))}
            >
              {p7BottomTabLabel(tab)}
            </button>
          )
        })}
        <button
          type="button"
          className="p7-bottom-panel-toggle"
          aria-label={ui.bottomOpen ? '折叠底部面板' : '展开底部面板'}
          onClick={() => onUiChange(setP7BottomOpen(ui, !ui.bottomOpen))}
        >
          {ui.bottomOpen ? <ChevronDown size={14} /> : <ChevronUp size={14} />}
        </button>
      </div>
      {ui.bottomOpen && (
        <div className="p7-bottom-content" role="tabpanel">
          {ui.bottomTab === 'terminal' && <P7UnavailableView title="终端" />}
          {ui.bottomTab === 'problems' && <P7UnavailableView title="问题" />}
          {ui.bottomTab === 'output' &&
            (outputLines.length === 0 ? (
              <div className="p7-muted-text">还没有操作记录。</div>
            ) : (
              outputLines.map((line, index) => (
                <div key={`${index}-${line}`} className="p7-log-line">
                  {line}
                </div>
              ))
            ))}
          {ui.bottomTab === 'agent-log' && (
            <>
              {eventLog.length === 0 && (
                <div className="p7-muted-text">
                  还没有事件。原始事件流只在此处显示；不会在界面上重放任何模拟内容。
                </div>
              )}
              {eventLog.map((line, index) => (
                <div key={`${index}-${line}`} className="p7-log-line">
                  {line}
                </div>
              ))}
            </>
          )}
        </div>
      )}
    </div>
  )
}

export function P7Editor(props: {
  readonly ui: P7ShellUiState
  readonly onUiChange: (next: P7ShellUiState) => void
  readonly presence: P7DataSourcePresence
  readonly messages: readonly DesktopMessage[]
  readonly messagesStatus: 'empty' | 'loading' | 'ready' | 'error'
  readonly messagesError: string | null
  readonly agentName: string
  readonly teamProjection: {
    readonly visible: boolean
    readonly parentLiveText: string
    readonly parentFinalAnswer: string | null
  }
  readonly liveProjection: DesktopInvocationLiveProjection
  readonly stopping: boolean
  readonly blackboard: PersonalTeamBlackboard | null
  readonly blackboardStatus: 'idle' | 'loading' | 'ready' | 'error'
  readonly eventLog: readonly string[]
  readonly outputLines: readonly string[]
  readonly workspaceFiles: P7WorkspaceFilesState
  readonly settings: P7SettingsCenterProps
  readonly componentSurface: P7WorkspaceComponentSurfaceProjection
  readonly settingsComponentSurface: P7WorkspaceComponentSurfaceProjection
  readonly workspaceBriefEnabled: boolean
  readonly hideBottomPanel: boolean
}) {
  const views: readonly P7CenterView[] =
    props.ui.centerView === 'settings'
      ? ['settings']
      : [
          'transcript',
          ...(props.workspaceBriefEnabled ? (['brief'] as const) : []),
          'code',
          'diff',
          ...(props.componentSurface.status === 'ready' ||
          props.componentSurface.status === 'safe-mode'
            ? (['component'] as const)
            : []),
        ]
  const viewIcons: Record<P7CenterView, LucideIcon> = {
    transcript: MessageSquare,
    brief: NotebookPen,
    code: FileCode2,
    diff: GitCompareArrows,
    component: Boxes,
    settings: Settings,
  }
  return (
    <section className="p7-workspace" aria-label="工作区">
      <div className="p7-tabbar" role="tablist" aria-label="编辑视图">
        {views.map((view) => {
          const availability = p7ViewAvailability(view, props.presence)
          const Icon = viewIcons[view]
          return (
            <button
              key={view}
              type="button"
              role="tab"
              className="p7-editor-tab"
              aria-selected={props.ui.centerView === view}
              aria-disabled={!availability.available}
              title={availability.available ? undefined : (availability.reason ?? undefined)}
              onClick={() => props.onUiChange(setP7CenterView(props.ui, view))}
            >
              <Icon size={14} />
              {p7CenterViewLabel(view)}
              {!availability.available && <span className="p7-row-meta">unavailable</span>}
            </button>
          )
        })}
      </div>
      <div className="p7-editor-views">
        {props.ui.centerView === 'transcript' && (
          <P7TranscriptView
            messages={props.messages}
            messagesStatus={props.messagesStatus}
            messagesError={props.messagesError}
            agentName={props.agentName}
            teamProjection={props.teamProjection}
            liveProjection={props.liveProjection}
            stopping={props.stopping}
          />
        )}
        {props.ui.centerView === 'brief' && (
          <P7BriefView blackboard={props.blackboard} blackboardStatus={props.blackboardStatus} />
        )}
        {props.ui.centerView === 'code' && <P7CodeView files={props.workspaceFiles} />}
        {props.ui.centerView === 'diff' && <P7UnavailableView title="审阅变更" />}
        {props.ui.centerView === 'component' && (
          <P7ComponentSurface projection={props.componentSurface} />
        )}
        {props.ui.centerView === 'settings' && (
          <P7SettingsCenter
            {...props.settings}
            componentSurface={props.settingsComponentSurface}
            onClose={() =>
              props.onUiChange({
                ...props.ui,
                activity: 'explorer',
                sidebarOpen: true,
                centerView: 'transcript',
              })
            }
          />
        )}
      </div>
      {!props.hideBottomPanel && (
        <P7BottomPanel
          ui={props.ui}
          onUiChange={props.onUiChange}
          presence={props.presence}
          eventLog={props.eventLog}
          outputLines={props.outputLines}
        />
      )}
    </section>
  )
}
