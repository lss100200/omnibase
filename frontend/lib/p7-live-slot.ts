/**
 * P7.0 Wave 1 — live/history slot state machine.
 *
 * Pure reducer for the workbench's blackboard-slot wiring. All transitions
 * that the subscription layer used to perform ad hoc (opening the live slot
 * on a new run, closing it on terminal, auto-following the run panel
 * selection, landing terminal blackboards) live here so the ordering rules
 * are unit-testable:
 *
 * - Terminal events NEVER open a live slot; the first event of a run may be
 *   a terminal snapshot, and duplicate/late terminal events after the slot
 *   closed must not reopen it.
 * - The final blackboard of a watched run is prefetched on terminal even
 *   while the run finishes in the background; display is conversation-gated
 *   by the projection, so the origin view reads it on return and other
 *   conversations never render it.
 * - The auto-follow selection is established from the run's origin even when
 *   the first event arrives while the view is parked elsewhere; the
 *   projection keeps it hidden outside that conversation, so a background
 *   run always leaves a recoverable selection. Manual selections are
 *   workspace-scoped: they never project into another workspace.
 * - A parked run's events (visible phase hidden as `idle` upstream) still
 *   open the live slot via identity gates, so returning to the origin
 *   conversation reads the correct board.
 *
 * The reducer returns effects (`loadLiveBoard` / `loadHistoryBoard` /
 * `refreshRunHistory`) that the wiring layer executes as IPC calls; the
 * wiring never decides slot transitions itself.
 */

import type { PersonalTeamBlackboard } from './desktop-bridge'

export interface P7LiveSlotState {
  /** Non-terminal run currently executing; null while idle or terminal. */
  readonly liveRunId: string | null
  /** Origin conversation key (`workspace:conversation`) of the live run. */
  readonly liveOriginKey: string | null
  /** Run highlighted in the run panel / feeding the history board. */
  readonly historyRunId: string | null
  /** Conversation key where the auto-follow was established. */
  readonly historyOriginKey: string | null
  /** Workspace where the selection was established (manual gating). */
  readonly historyOriginWorkspaceId: string | null
  /** True once the user explicitly picked a run in the panel. */
  readonly historyIsManual: boolean
}

export function createP7LiveSlotState(): P7LiveSlotState {
  return {
    liveRunId: null,
    liveOriginKey: null,
    historyRunId: null,
    historyOriginKey: null,
    historyOriginWorkspaceId: null,
    historyIsManual: false,
  }
}

/** Clear the live slot only (new run attempt, terminal close). */
export function invalidateP7LiveSlot(state: P7LiveSlotState): P7LiveSlotState {
  return { ...state, liveRunId: null, liveOriginKey: null }
}

export interface P7LiveSlotEffects {
  readonly loadLiveBoard: boolean
  readonly loadHistoryBoard: boolean
  readonly refreshRunHistory: boolean
}
const NO_EFFECTS: P7LiveSlotEffects = {
  loadLiveBoard: false,
  loadHistoryBoard: false,
  refreshRunHistory: false,
}

export function reduceP7LiveSlotEvent(
  state: P7LiveSlotState,
  input: {
    readonly eventRunId: string | null
    readonly eventWorkspaceId: string | null
    readonly eventConversationId: string | null
    readonly isTerminal: boolean
    readonly bindsLiveRun: boolean
    readonly viewWorkspaceId: string | null
    readonly viewConversationId: string | null
    /** blackboard / plan_transition / proposal events change the board. */
    readonly boardChanged: boolean
  },
): { readonly state: P7LiveSlotState; readonly effects: P7LiveSlotEffects } {
  if (!input.bindsLiveRun || input.eventRunId === null) {
    return { state, effects: NO_EFFECTS }
  }
  const eventKey = `${input.eventWorkspaceId}:${input.eventConversationId}`

  // Terminal events never open a slot. Closing the live slot when the
  // terminal belongs to it; a duplicate/late terminal after the close (or a
  // terminal for another run) leaves the slot untouched.
  if (input.isTerminal) {
    const closingLive = input.eventRunId === state.liveRunId
    // A terminal-first event (no live slot was ever opened) lands an
    // origin-scoped history selection and loads the final board. It may
    // replace a previous AUTO selection (a new run's first visible event
    // being terminal means it is the current run); only an explicit manual
    // selection is protected. The live slot stays closed and the projection
    // keeps the selection hidden outside its origin.
    const autoLanding = !state.historyIsManual && state.liveRunId === null
    // The final board is prefetched for any watched run regardless of the
    // current view: display is conversation-gated by the projection, so a
    // background terminal can safely land the board and the origin view
    // reads it immediately when the user returns.
    const historyRefresh = input.eventRunId === state.historyRunId
    return {
      state: closingLive
        ? invalidateP7LiveSlot(state)
        : autoLanding
          ? {
              ...state,
              historyRunId: input.eventRunId,
              historyOriginKey: eventKey,
              historyOriginWorkspaceId: input.eventWorkspaceId,
            }
          : state,
      effects: {
        loadLiveBoard: false,
        loadHistoryBoard: historyRefresh || autoLanding,
        refreshRunHistory: input.eventWorkspaceId === input.viewWorkspaceId,
      },
    }
  }

  // Progress/board events for the already-open live run.
  if (input.eventRunId === state.liveRunId) {
    return {
      state,
      effects: { ...NO_EFFECTS, loadLiveBoard: input.boardChanged },
    }
  }

  // New live run (non-terminal only). The auto-follow is established from
  // the event's origin whenever the user has no manual selection — even when
  // the first event arrives while the view is parked elsewhere. The
  // projection layer is what hides it in other conversations, so a run that
  // starts in the background still leaves a recoverable selection for its
  // origin view (and its terminal still prefetches the final board).
  return {
    state: {
      ...state,
      liveRunId: input.eventRunId,
      liveOriginKey: eventKey,
      historyRunId: state.historyIsManual ? state.historyRunId : input.eventRunId,
      historyOriginKey: state.historyIsManual ? state.historyOriginKey : eventKey,
      historyOriginWorkspaceId: state.historyIsManual
        ? state.historyOriginWorkspaceId
        : input.eventWorkspaceId,
    },
    effects: {
      loadLiveBoard: true,
      loadHistoryBoard: false,
      refreshRunHistory: input.eventWorkspaceId === input.viewWorkspaceId,
    },
  }
}

/** User clicked a run in the run panel: explicit, workspace-level choice. */
export function selectP7HistoryRun(
  state: P7LiveSlotState,
  runId: string,
  viewWorkspaceId: string | null,
  viewConversationId: string | null,
): P7LiveSlotState {
  return {
    ...state,
    historyRunId: runId,
    historyOriginKey: `${viewWorkspaceId}:${viewConversationId}`,
    historyOriginWorkspaceId: viewWorkspaceId,
    historyIsManual: true,
  }
}

/**
 * What the current view may display. The live slot is current only in the
 * run's origin conversation. The history selection bypasses only the
 * conversation restriction when manual — it never crosses the workspace
 * boundary, so a workspace switch cannot project the previous workspace's
 * blackboard even before the reset effect runs.
 */
export function p7LiveSlotViewProjection(
  state: P7LiveSlotState,
  viewWorkspaceId: string | null,
  viewConversationId: string | null,
): {
  readonly liveCurrent: boolean
  readonly selectionRunId: string | null
  readonly selectionVisible: boolean
} {
  const viewKey = `${viewWorkspaceId}:${viewConversationId}`
  const liveCurrent = state.liveRunId !== null && state.liveOriginKey === viewKey
  const selectionVisible =
    state.historyRunId !== null &&
    state.historyOriginKey !== null &&
    (state.historyIsManual
      ? state.historyOriginWorkspaceId !== null &&
        state.historyOriginWorkspaceId === viewWorkspaceId
      : state.historyOriginKey === viewKey)
  return {
    liveCurrent,
    selectionRunId: selectionVisible ? state.historyRunId : null,
    selectionVisible,
  }
}

/**
 * Display-side identity guard: the history board may only be shown while it
 * belongs to the run currently selected. A stale payload (auto-follow moved
 * to another run, or a failed reload) must never render under a different
 * run's selection.
 */
export function p7HistoryBoardForSelection(
  selectionRunId: string | null,
  board: PersonalTeamBlackboard | null,
): PersonalTeamBlackboard | null {
  if (selectionRunId === null || board === null) return null
  return board.teamRunId === selectionRunId ? board : null
}

/**
 * Whether a history selection should be cleared after a run list reload.
 * Only selections whose origin workspace matches the workspace being loaded
 * may be validated against that list: a background run's origin-scoped
 * selection must survive while its origin workspace is not the one being
 * viewed, or its final board would never be recoverable on return.
 */
export function p7SelectionStaleInWorkspace(input: {
  readonly historyWorkspaceId: string | null
  readonly loadedWorkspaceId: string | null
  readonly historyRunId: string | null
  readonly runs: readonly { readonly id: string }[]
}): boolean {
  if (input.historyRunId === null) return false
  if (input.historyWorkspaceId !== input.loadedWorkspaceId) return false
  return !input.runs.some((run) => run.id === input.historyRunId)
}
