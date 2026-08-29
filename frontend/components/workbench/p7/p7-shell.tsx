'use client'

import {
  ChevronDown,
  Command,
  GitBranch,
  NotebookTabs,
  PanelBottom,
  PanelLeft,
  PanelRight,
  Play,
  Search,
  Settings,
  ShieldCheck,
  Sparkles,
  Files,
  Bot,
} from 'lucide-react'
import { useMemo, useState } from 'react'
import type {
  DesktopConversation,
  DesktopInvocationLiveProjection,
  DesktopMessage,
  DesktopOwner,
  DesktopProvider,
  DesktopReasoningGear,
  DesktopTeamRun,
  DesktopTeamRunBudget,
  DesktopThinkingDepth,
  DesktopWorkspace,
  PersonalTeamBlackboard,
} from '@/lib/desktop-bridge'
import type { DesktopTeamLiveState } from '@/lib/desktop-team-lifecycle'
import {
  P7_OMNIA_IMAGES,
  createP7ShellUiState,
  expandP7Omnia,
  minimizeP7Omnia,
  openP7Blackboard,
  p7ActivityLabel,
  p7LiveActive,
  p7LivePendingCollaborations,
  p7OmniaStateForLive,
  p7RunningCount,
  selectP7BottomTab,
  setP7AgentPanelOpen,
  setP7BottomOpen,
  setP7CenterView,
  toggleP7Activity,
  toggleP7OmniaPopover,
  type P7Activity,
  type P7DataSourcePresence,
  type P7LiveReference,
  type P7OmniaSnapshot,
  type P7ShellUiState,
} from '@/lib/p7-workbench-shell'
import { p7WorkspaceFilesAuthorized, type P7WorkspaceFilesState } from '@/lib/p7-workspace-files'
import { P7Sidebar } from './p7-sidebar'
import { P7Editor } from './p7-editor'
import { P7AgentPanel } from './p7-agent-panel'

export interface P7ProviderForm {
  readonly displayName: string
  readonly baseUrl: string
  readonly apiKey: string
  readonly modelName: string
  readonly gear: DesktopReasoningGear
  readonly thinkingDepth: DesktopThinkingDepth
  readonly timeoutSeconds: number
  readonly allowLoopbackHttp: boolean
  readonly isDefault: boolean
  readonly isEnabled: boolean
}

export interface P7WorkbenchProps {
  readonly version: string
  readonly owner: DesktopOwner
  readonly chinese: boolean
  readonly zoom: number
  readonly onZoomChange: (next: number) => void

  readonly workspaces: readonly DesktopWorkspace[]
  readonly workspaceId: string | null
  readonly conversations: readonly DesktopConversation[]
  readonly conversationId: string | null
  readonly onSelectWorkspace: (workspaceId: string) => void
  readonly onCreateWorkspace: (name: string) => void
  readonly onSelectConversation: (conversationId: string) => void
  readonly onCreateConversation: () => void
  readonly onArchiveConversation: (conversationId: string) => void
  readonly workspaceNameInput: string
  readonly onWorkspaceNameInputChange: (name: string) => void

  readonly workspaceFiles: P7WorkspaceFilesState
  readonly onAuthorizeWorkspaceFiles: () => void
  readonly onReleaseWorkspaceFiles: () => void
  readonly onToggleWorkspaceDirectory: (directoryPath: string, expanded: boolean) => void
  readonly onOpenWorkspaceFile: (path: string) => void

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

  readonly teamLive: DesktopTeamLiveState
  readonly taskText: string | null
  readonly teamMode: boolean
  readonly onTeamModeChange: (enabled: boolean) => void
  readonly allowedSpecialists: readonly string[]
  readonly onAllowedSpecialistsChange: (roles: readonly string[]) => void
  readonly teamBudget: DesktopTeamRunBudget
  readonly appendCalls: string
  readonly onAppendCallsChange: (calls: string) => void
  readonly teamAppendBudgetTarget: {
    readonly workspaceId: string
    readonly teamRunId: string
  } | null
  readonly onAppendBudget: (maximumProviderCalls: number) => void

  readonly runHistory: readonly DesktopTeamRun[]
  readonly runHistoryStatus: 'idle' | 'loading' | 'ready' | 'error'
  readonly selectedRunId: string | null
  readonly onSelectRun: (teamRunId: string) => void
  /** Effective brief board: the live run's board while a run executes, else the browsed history run's board. */
  readonly blackboard: PersonalTeamBlackboard | null
  readonly blackboardStatus: 'idle' | 'loading' | 'ready' | 'error'
  /** The currently executing run; only its board may drive OMNIA. */
  readonly liveRunId: string | null
  readonly liveBlackboard: PersonalTeamBlackboard | null
  /** True while the user views the live run's origin conversation. */
  readonly liveCurrent: boolean

  readonly draft: string
  readonly onDraftChange: (draft: string) => void
  readonly onSend: () => void
  readonly onRetry: () => void
  readonly onStop: () => void
  readonly sendBlocked: boolean
  readonly stopVisible: boolean

  readonly providerForm: P7ProviderForm
  readonly onProviderFormChange: (patch: Partial<P7ProviderForm>) => void
  readonly onSaveProvider: () => void
  readonly submitting: boolean
  readonly testResult: string | null
  readonly providers: readonly DesktopProvider[]
  readonly onTestProvider: (providerId: string) => void

  readonly eventLog: readonly string[]
  readonly outputLines: readonly string[]

  /** True while both bridge event subscriptions are active. */
  readonly bridgeSubscribed: boolean

  /** Real live invocation reference (workspace/conversation-bound stream). */
  readonly live: P7LiveReference
}

function P7Titlebar({
  version,
  chinese,
  ui,
  onUiChange,
  zoom,
  onZoomChange,
  onPaletteOpen,
}: {
  readonly version: string
  readonly chinese: boolean
  readonly ui: P7ShellUiState
  readonly onUiChange: (next: P7ShellUiState) => void
  readonly zoom: number
  readonly onZoomChange: (next: number) => void
  readonly onPaletteOpen: () => void
}) {
  return (
    <header className="p7-titlebar">
      <div className="p7-titlebar-brand">
        <img src="/brand/omnibase-logo-icon.png" alt="OmniBase" />
        <span className="p7-titlebar-brand-text">
          OmniBase · {chinese ? '桌面工作台' : 'Desktop Workbench'}
        </span>
        <span className="p7-titlebar-brand-version">v{version}</span>
      </div>
      <button type="button" className="p7-command" onClick={onPaletteOpen}>
        <Search size={14} />
        <span className="p7-row-text">{chinese ? '搜索命令…' : 'Search commands…'}</span>
        <span className="p7-command-hint">Ctrl K</span>
      </button>
      <div className="p7-titlebar-actions">
        <button
          type="button"
          className="p7-icon-button"
          title="切换主侧栏"
          aria-label="切换主侧栏"
          aria-pressed={ui.sidebarOpen}
          onClick={() => onUiChange({ ...ui, sidebarOpen: !ui.sidebarOpen })}
        >
          <PanelLeft size={15} />
        </button>
        <button
          type="button"
          className="p7-icon-button"
          title="切换底部面板"
          aria-label="切换底部面板"
          aria-pressed={ui.bottomOpen}
          onClick={() => onUiChange(setP7BottomOpen(ui, !ui.bottomOpen))}
        >
          <PanelBottom size={15} />
        </button>
        <button
          type="button"
          className="p7-icon-button"
          title="切换 Agent 面板"
          aria-label="切换 Agent 面板"
          aria-pressed={ui.agentPanelOpen}
          onClick={() => onUiChange(setP7AgentPanelOpen(ui, !ui.agentPanelOpen))}
        >
          <PanelRight size={15} />
        </button>
        <button
          type="button"
          className="p7-text-button"
          title="缩小界面文字"
          onClick={() => onZoomChange(Math.max(90, zoom - 10))}
        >
          A−
        </button>
        <button
          type="button"
          className="p7-text-button"
          title="放大界面文字"
          onClick={() => onZoomChange(Math.min(140, zoom + 10))}
        >
          A+
        </button>
      </div>
    </header>
  )
}

function P7ActivityBar({
  ui,
  onUiChange,
  activeRunCount,
  runningCount,
  omnia,
}: {
  readonly ui: P7ShellUiState
  readonly onUiChange: (next: P7ShellUiState) => void
  readonly activeRunCount: number
  readonly runningCount: number
  readonly omnia: P7OmniaSnapshot
}) {
  const activities: readonly {
    readonly id: P7Activity
    readonly icon: typeof Files
    readonly badge: number | null
  }[] = [
    { id: 'explorer', icon: Files, badge: null },
    { id: 'search', icon: Search, badge: null },
    { id: 'source', icon: GitBranch, badge: null },
    { id: 'run', icon: Play, badge: activeRunCount > 0 ? activeRunCount : null },
    { id: 'agents', icon: Bot, badge: runningCount > 0 ? runningCount : null },
    { id: 'blackboard', icon: NotebookTabs, badge: null },
  ]
  return (
    <nav className="p7-activity" aria-label="活动栏">
      {activities.map(({ id, icon: Icon, badge }) => (
        <button
          key={id}
          type="button"
          className="p7-activity-button"
          aria-label={p7ActivityLabel(id)}
          title={p7ActivityLabel(id)}
          aria-pressed={ui.activity === id && ui.sidebarOpen}
          onClick={() => {
            const next = toggleP7Activity(ui, id)
            onUiChange(id === 'blackboard' ? openP7Blackboard(next) : next)
          }}
        >
          <Icon size={16} />
          {badge !== null && <span className="p7-activity-badge">{badge}</span>}
        </button>
      ))}
      <div className="p7-activity-spacer" />
      <button
        type="button"
        className="p7-activity-button"
        aria-label={`OMNIA · ${omnia.statusText}`}
        title={`OMNIA · ${omnia.statusText}`}
        onClick={() => onUiChange(expandP7Omnia(ui))}
      >
        <Sparkles size={16} />
        {omnia.dotTone === 'amber' && <span className="p7-activity-dot p7-dot-amber" />}
      </button>
      <button
        type="button"
        className="p7-activity-button"
        aria-label="设置"
        title="设置"
        aria-pressed={ui.activity === 'settings' && ui.sidebarOpen}
        onClick={() => onUiChange(toggleP7Activity(ui, 'settings'))}
      >
        <Settings size={16} />
      </button>
    </nav>
  )
}

function P7Statusbar({
  ownerName,
  workspaceName,
  conversationCount,
  runningCount,
  bridgeSubscribed,
  onOpenAgentLog,
  onOpenOmnia,
  onZoomChange,
  zoom,
}: {
  readonly ownerName: string
  readonly workspaceName: string
  readonly conversationCount: number
  readonly runningCount: number
  readonly bridgeSubscribed: boolean
  readonly onOpenAgentLog: () => void
  readonly onOpenOmnia: () => void
  readonly onZoomChange: (next: number) => void
  readonly zoom: number
}) {
  return (
    <footer className="p7-statusbar">
      <div className="p7-statusbar-group">
        <span className="p7-status-item p7-status-static">
          <GitBranch size={11} />
          {workspaceName}
        </span>
        <span className="p7-status-item p7-status-static">
          {conversationCount} {conversationCount === 1 ? '个会话' : '个会话'}
        </span>
        <span className="p7-status-item p7-status-static">
          <ShieldCheck size={11} />
          原生控制
        </span>
      </div>
      <div className="p7-statusbar-group">
        {runningCount > 0 && (
          <span className="p7-status-item p7-status-static">
            <Bot size={11} />
            {runningCount} 运行中
          </span>
        )}
        <button type="button" className="p7-status-item" onClick={onOpenAgentLog}>
          <Sparkles size={11} />
          {bridgeSubscribed ? '事件通道已订阅' : '事件通道未连接'}
        </button>
        <button type="button" className="p7-status-item" onClick={onOpenOmnia}>
          OMNIA
        </button>
        <span className="p7-status-item p7-status-static">{ownerName}</span>
        <button
          type="button"
          className="p7-status-item"
          onClick={() => onZoomChange(Math.max(90, zoom - 10))}
        >
          A−
        </button>
        <button
          type="button"
          className="p7-status-item"
          onClick={() => onZoomChange(Math.min(140, zoom + 10))}
        >
          A+
        </button>
      </div>
    </footer>
  )
}

function P7OmniaWidget({
  ui,
  onUiChange,
  omnia,
}: {
  readonly ui: P7ShellUiState
  readonly onUiChange: (next: P7ShellUiState) => void
  readonly omnia: P7OmniaSnapshot
}) {
  return (
    <div
      className={`p7-omnia-widget${ui.omniaMinimized ? 'p7-minimized' : ''}`}
      role="complementary"
      aria-label={`OMNIA · ${omnia.statusText}`}
    >
      {ui.omniaPopoverOpen && !ui.omniaMinimized && (
        <div className="p7-omnia-popover">
          <div className="p7-omnia-popover-head">
            <span>OMNIA</span>
            <button
              type="button"
              className="p7-agent-control"
              aria-label="最小化 OMNIA"
              onClick={() => onUiChange(minimizeP7Omnia(ui))}
            >
              <ChevronDown size={14} />
            </button>
          </div>
          <div className="p7-omnia-popover-body">
            <span className={`p7-dot p7-dot-${omnia.dotTone}`} />
            <span>{omnia.statusText}</span>
          </div>
          <div className="p7-omnia-popover-foot">
            <span>桌面本地</span>
            <span>{omnia.state}</span>
          </div>
        </div>
      )}
      <button
        type="button"
        className="p7-omnia-trigger"
        aria-label={omnia.altText}
        title={omnia.statusText}
        onClick={() => onUiChange(toggleP7OmniaPopover(ui))}
      >
        <img src={P7_OMNIA_IMAGES[omnia.state]} alt={omnia.altText} />
        <span className={`p7-omnia-state-dot p7-dot-${omnia.dotTone}`} />
      </button>
    </div>
  )
}

interface P7CommandEntry {
  readonly label: string
  readonly hint: string
  readonly run: (ui: P7ShellUiState) => P7ShellUiState | null
}

export function P7WorkbenchShell(props: P7WorkbenchProps) {
  const [ui, setUi] = useState<P7ShellUiState>(createP7ShellUiState)
  const [paletteOpen, setPaletteOpen] = useState(false)
  const [paletteQuery, setPaletteQuery] = useState('')

  const presence = useMemo(() => {
    // P7.1 unlocks Code only while an Owner-selected native authorization is
    // live. Every other previously unavailable catalog stays unavailable.
    return {
      files: p7WorkspaceFilesAuthorized(props.workspaceFiles),
      diff: false,
      terminal: false,
      problems: false,
      output: false,
      search: false,
      source: false,
    } satisfies P7DataSourcePresence
  }, [props.workspaceFiles])

  const runningCount = p7RunningCount({
    teamPhase: props.teamLive.phase,
    // Only the origin view sees the live invocation; a parked view must not
    // claim it as running here.
    live: props.liveProjection.visible
      ? props.live
      : { conversationId: null, invocationId: null, phase: 'idle' },
  })

  const liveActive = p7LiveActive({
    liveVisible: props.liveProjection.visible,
    live: props.live,
  })

  const omnia = p7OmniaStateForLive({
    teamPhase: props.teamLive.phase,
    teamRunState: props.teamLive.runState,
    livePhase: props.live.phase,
    liveVisible: props.liveProjection.visible,
    pendingCollaborations: p7LivePendingCollaborations(
      props.liveCurrent,
      props.liveRunId,
      props.liveBlackboard,
    ),
  })

  const activeRunCount = props.runHistory.filter((run) => {
    return run.state === 'preparing' || run.state === 'running' || run.state === 'cancelling'
  }).length

  const workspaceName =
    props.workspaces.find((item) => item.id === props.workspaceId)?.name ?? '未选择工作空间'

  const commands: readonly P7CommandEntry[] = [
    {
      label: '打开任务简报',
      hint: 'Artifact',
      run: (current) => openP7Blackboard(current),
    },
    {
      label: '切换底部面板',
      hint: 'Ctrl `',
      run: (current) => setP7BottomOpen(current, !current.bottomOpen),
    },
    {
      label: '打开 Agent Log',
      hint: '事件流',
      run: (current) => selectP7BottomTab(current, 'agent-log'),
    },
    {
      label: '切换 Agent 面板',
      hint: 'Ctrl L',
      run: (current) => setP7AgentPanelOpen(current, !current.agentPanelOpen),
    },
    {
      label: '新建会话',
      hint: '会话',
      run: () => {
        props.onCreateConversation()
        return null
      },
    },
  ]
  const filteredCommands = commands.filter((command) =>
    command.label.toLowerCase().includes(paletteQuery.trim().toLowerCase()),
  )

  const updateUi = (next: P7ShellUiState) => setUi(next)

  return (
    <div
      className={`p7-root${ui.agentPanelOpen ? '' : 'p7-agent-closed-body'}`}
      style={{ fontSize: `${(16 * props.zoom) / 100}px` }}
      onKeyDown={(event) => {
        if (event.key === 'k' && (event.ctrlKey || event.metaKey)) {
          event.preventDefault()
          setPaletteOpen((open) => !open)
          setPaletteQuery('')
          return
        }
        if (event.key === 'l' && (event.ctrlKey || event.metaKey)) {
          event.preventDefault()
          setUi((current) => setP7AgentPanelOpen(current, !current.agentPanelOpen))
          return
        }
        if (event.key === '`' && (event.ctrlKey || event.metaKey)) {
          event.preventDefault()
          setUi((current) => setP7BottomOpen(current, !current.bottomOpen))
        }
      }}
    >
      <P7Titlebar
        version={props.version}
        chinese={props.chinese}
        ui={ui}
        onUiChange={updateUi}
        zoom={props.zoom}
        onZoomChange={props.onZoomChange}
        onPaletteOpen={() => {
          setPaletteOpen(true)
          setPaletteQuery('')
        }}
      />
      <div
        className={`p7-shell${ui.sidebarOpen ? '' : 'p7-sidebar-closed'}${ui.agentPanelOpen ? '' : 'p7-agent-closed'}`}
      >
        <P7ActivityBar
          ui={ui}
          onUiChange={updateUi}
          activeRunCount={activeRunCount}
          runningCount={runningCount}
          omnia={omnia}
        />
        {ui.sidebarOpen && (
          <P7Sidebar
            activity={ui.activity}
            {...props}
            onOpenWorkspaceFile={(path) => {
              props.onOpenWorkspaceFile(path)
              updateUi(setP7CenterView(ui, 'code'))
            }}
          />
        )}
        <P7Editor
          ui={ui}
          onUiChange={updateUi}
          presence={presence}
          messages={props.messages}
          messagesStatus={props.messagesStatus}
          messagesError={props.messagesError}
          agentName={props.agentName}
          teamProjection={props.teamProjection}
          liveProjection={props.liveProjection}
          stopping={props.stopping}
          blackboard={props.blackboard}
          blackboardStatus={props.blackboardStatus}
          eventLog={props.eventLog}
          outputLines={props.outputLines}
          workspaceFiles={props.workspaceFiles}
        />
        {ui.agentPanelOpen && (
          <P7AgentPanel
            agentName={props.agentName}
            teamLive={props.teamLive}
            teamMode={props.teamMode}
            taskText={props.taskText}
            liveProjection={props.liveProjection}
            draft={props.draft}
            onDraftChange={props.onDraftChange}
            onSend={props.onSend}
            onRetry={props.onRetry}
            onStop={props.onStop}
            sendBlocked={props.sendBlocked}
            stopVisible={props.stopVisible}
            onClose={() => updateUi(setP7AgentPanelOpen(ui, false))}
            omnia={omnia}
            liveActive={liveActive}
          />
        )}
        <P7OmniaWidget ui={ui} onUiChange={updateUi} omnia={omnia} />
      </div>
      <P7Statusbar
        ownerName={props.owner.displayName}
        workspaceName={workspaceName}
        conversationCount={props.conversations.filter((item) => item.state === 'active').length}
        runningCount={runningCount}
        bridgeSubscribed={props.bridgeSubscribed}
        onOpenAgentLog={() => updateUi(selectP7BottomTab(ui, 'agent-log'))}
        onOpenOmnia={() => updateUi(expandP7Omnia(ui))}
        onZoomChange={props.onZoomChange}
        zoom={props.zoom}
      />
      {paletteOpen && (
        <div
          className="p7-palette-backdrop"
          onClick={() => setPaletteOpen(false)}
          role="presentation"
        >
          <div
            className="p7-palette"
            role="dialog"
            aria-label="命令面板"
            onClick={(event) => event.stopPropagation()}
          >
            <div className="p7-palette-input">
              <Command size={14} />
              <input
                autoFocus
                value={paletteQuery}
                onChange={(event) => setPaletteQuery(event.target.value)}
                placeholder="搜索命令…"
              />
            </div>
            <div className="p7-palette-commands">
              {filteredCommands.length === 0 && (
                <div className="p7-muted-text p7-view-pad">没有匹配的命令。</div>
              )}
              {filteredCommands.map((command) => (
                <button
                  key={command.label}
                  type="button"
                  className="p7-palette-command"
                  onClick={() => {
                    const next = command.run(ui)
                    if (next !== null) updateUi(next)
                    setPaletteOpen(false)
                  }}
                >
                  <span className="p7-row-text">{command.label}</span>
                  <span className="p7-palette-command-hint">{command.hint}</span>
                </button>
              ))}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
