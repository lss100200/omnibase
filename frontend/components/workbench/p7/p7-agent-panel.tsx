'use client'

import { RotateCcw, Send, Square, X } from 'lucide-react'
import type { DesktopInvocationLiveProjection } from '@/lib/desktop-bridge'
import type { DesktopTeamLiveState } from '@/lib/desktop-team-lifecycle'
import { projectP7AgentFeed, type P7OmniaSnapshot } from '@/lib/p7-workbench-shell'

export function P7AgentPanel(props: {
  readonly agentName: string
  readonly teamLive: DesktopTeamLiveState
  readonly teamMode: boolean
  readonly taskText: string | null
  readonly liveProjection: DesktopInvocationLiveProjection
  readonly draft: string
  readonly onDraftChange: (draft: string) => void
  readonly onSend: () => void
  readonly onRetry: () => void
  readonly onStop: () => void
  readonly sendBlocked: boolean
  readonly stopVisible: boolean
  readonly onClose: () => void
  readonly omnia: P7OmniaSnapshot
  readonly liveActive: boolean
}) {
  const feed = projectP7AgentFeed({
    agentName: props.agentName,
    teamPhase: props.teamLive.phase,
    teamRunState: props.teamLive.runState,
    taskText: props.taskText,
    nodes: props.teamLive.nodes,
    collaborationLines: props.teamLive.collaborationLines,
    planRevisionId: props.teamLive.planRevisionId,
    waveId: props.teamLive.waveId,
    declaredExecution: props.teamLive.declaredExecution,
    effectiveExecution: props.teamLive.effectiveExecution,
    planSummary: props.teamLive.planSummary,
    parentFinalAnswer: props.teamLive.parentFinalAnswer,
    liveText: props.liveProjection.liveText,
    liveVisible: props.liveProjection.visible,
    liveActive: props.liveActive,
    consumedProviderCalls: props.teamLive.consumedProviderCalls,
    maximumProviderCalls: props.teamLive.maximumProviderCalls,
  })

  const teamActive = props.teamLive.phase !== 'idle'

  return (
    <aside className="p7-agent" aria-label="Agent 面板">
      <div className="p7-agent-header">
        <span className={`p7-live-dot p7-dot-${props.omnia.dotTone}`} />
        <span className="p7-agent-title">
          {props.teamMode && teamActive && !props.liveActive
            ? `父 Agent · 团队协作`
            : props.agentName}
        </span>
        {props.stopVisible && (
          <button
            type="button"
            className="p7-agent-control"
            aria-label="停止"
            title="停止"
            onClick={props.onStop}
          >
            <Square size={14} />
          </button>
        )}
        <button
          type="button"
          className="p7-agent-control"
          aria-label="关闭 Agent 面板"
          title="关闭 Agent 面板"
          onClick={props.onClose}
        >
          <X size={14} />
        </button>
      </div>
      <div className="p7-agent-feed" aria-live="polite">
        {feed.length === 1 && feed[0]?.kind === 'task' && feed[0].detail === null && (
          <div className="p7-agent-empty">
            输入一条指令，父 Agent 会以单 Agent 模式生成；开启团队协作后，父 Agent
            会编制员工并交给宿主校验执行。
          </div>
        )}
        {feed.map((item) => {
          if (item.kind === 'task') {
            return (
              <div key={item.key} className="p7-agent-task">
                <h2>{item.title}</h2>
                {item.detail !== null && <p>{item.detail}</p>}
              </div>
            )
          }
          if (item.kind === 'event') {
            return (
              <div
                key={item.key}
                className={`p7-event-row${
                  item.tone === 'current'
                    ? 'p7-current'
                    : item.tone === 'ok'
                      ? 'p7-ok'
                      : item.tone === 'error'
                        ? 'p7-error'
                        : ''
                }`}
              >
                <span className="p7-event-dot" />
                <div className="p7-event-body">
                  <span className="p7-event-label">{item.label}</span>
                  {item.meta !== null && <span className="p7-event-meta">{item.meta}</span>}
                </div>
              </div>
            )
          }
          return (
            <div key={item.key} className="p7-agent-result">
              <div className="p7-agent-result-label">父 Agent 最终回答</div>
              <div className="p7-agent-result-answer">{item.answer}</div>
              {item.meta !== null && <div className="p7-agent-result-meta">{item.meta}</div>}
            </div>
          )
        })}
      </div>
      <form
        className="p7-composer"
        onSubmit={(event) => {
          event.preventDefault()
          if (props.sendBlocked || props.draft.trim() === '') return
          props.onSend()
        }}
      >
        <label className="p7-sr-only" htmlFor="p7-composer-input">
          给 Agent 的指令
        </label>
        <textarea
          id="p7-composer-input"
          value={props.draft}
          onChange={(event) => props.onDraftChange(event.target.value)}
          placeholder="向父 Agent 提问…"
        />
        <div className="p7-composer-toolbar">
          <button
            type="button"
            className="p7-composer-tool"
            disabled={props.sendBlocked}
            onClick={props.onRetry}
          >
            <RotateCcw size={13} />
            重试
          </button>
          {props.stopVisible && (
            <button
              type="button"
              className="p7-composer-tool p7-tool-danger"
              onClick={props.onStop}
            >
              <Square size={13} />
              停止
            </button>
          )}
          <span className="p7-row-meta">Agent · 本地工作区</span>
          <button
            type="submit"
            className="p7-send-button"
            aria-label="发送"
            disabled={props.sendBlocked || props.draft.trim() === ''}
          >
            <Send size={14} />
          </button>
        </div>
      </form>
    </aside>
  )
}
