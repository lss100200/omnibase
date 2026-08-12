/**
 * Invocation ownership guard for the Agent workbench (P5.4D Round 2 P1-5).
 *
 * Serializes Stop -> immediate reinvoke: every invocation gets a unique
 * generation plus its own AbortController, and only the CURRENT
 * generation/controller pair may mutate the running/cancelling state or be
 * settled by a finally block.  A stale invocation's catch/finally can never
 * clear a newer invocation's controller or overwrite its state.
 *
 * Phases:
 * - idle:      no invocation in flight; begin() is allowed.
 * - running:   an invocation owns the controller; stop() aborts it.
 * - cancelling: stop() was pressed; begin() is REFUSED until the old
 *               invocation settles, so the UI never re-opens Invoke before
 *               the previous promise actually finished, and Stop never
 *               claims the UI is fully idle early.
 */
export type InvocationPhase = 'idle' | 'running' | 'cancelling'

export class InvocationGuard {
  private _generation = 0
  private _phase: InvocationPhase = 'idle'
  private _controller: AbortController | null = null

  get phase(): InvocationPhase {
    return this._phase
  }

  get generation(): number {
    return this._generation
  }

  /** Start a new invocation; returns a fresh controller when idle. */
  begin(): { generation: number; controller: AbortController } | null {
    if (this._phase !== 'idle') return null
    this._generation += 1
    const controller = new AbortController()
    this._controller = controller
    this._phase = 'running'
    return { generation: this._generation, controller }
  }

  /**
   * Request cancellation: abort the current controller and move to
   * cancelling.  Returns the aborted controller (null when idle).
   */
  stop(): AbortController | null {
    if (this._phase === 'idle') return null
    this._phase = 'cancelling'
    const controller = this._controller
    controller?.abort()
    return controller
  }

  /**
   * Abort and invalidate the current generation immediately. Used when the
   * authenticated tenant/user scope changes and no old callback may render in
   * the new scope. A later stale finally is harmless because its generation
   * and controller no longer match.
   */
  invalidate(): AbortController | null {
    const controller = this._controller
    controller?.abort()
    this._generation += 1
    this._controller = null
    this._phase = 'idle'
    return controller
  }

  /**
   * Idempotent settle: only the current generation AND the exact controller
   * that began that generation may clear the guard.  A stale finally (old
   * generation or a replaced controller) is ignored.
   */
  settle(generation: number, controller: AbortController): void {
    if (generation !== this._generation) return
    if (controller !== this._controller) return
    this._controller = null
    this._phase = 'idle'
  }

  /** True when callbacks from this invocation may still update the UI. */
  isCurrent(generation: number): boolean {
    return generation === this._generation && this._phase !== 'idle'
  }
}
