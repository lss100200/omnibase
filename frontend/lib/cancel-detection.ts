/**
 * User-initiated cancellation detection for the Agent workbench.
 *
 * Two distinct paths must render "Invocation cancelled.":
 * 1. the workbench's own Stop button aborts the fetch (DOMException AbortError);
 * 2. the backend SSE stream emits a `cancelled` event — its payload carries
 *    only the invocation id (no `code`), so the client raises
 *    `Error("cancelled")`, while a stream that surfaces the legacy error
 *    event may carry `agent_alpha_cancelled`.
 */
export function isUserCancelledError(error: unknown): boolean {
  if (error instanceof DOMException && error.name === 'AbortError') {
    return true
  }
  if (error instanceof Error) {
    return error.message === 'agent_alpha_cancelled' || error.message === 'cancelled'
  }
  return false
}
