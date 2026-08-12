import assert from 'node:assert/strict'
import { test } from 'node:test'

import { InvocationGuard } from './invocation-state'

test('begin returns a fresh controller when idle', () => {
  const guard = new InvocationGuard()
  const first = guard.begin()
  assert.ok(first)
  assert.equal(guard.phase, 'running')
  assert.equal(guard.generation, 1)
  const second = guard.begin()
  assert.equal(second, null, 'begin is refused while running')
})

test('stop moves to cancelling and refuses new invocations until settle', () => {
  const guard = new InvocationGuard()
  const a = guard.begin()!
  const controller = guard.stop()
  assert.equal(controller, a.controller)
  assert.equal(controller.signal.aborted, true)
  assert.equal(guard.phase, 'cancelling')
  // Stop is NOT idle: a new invoke is blocked while A is still finishing.
  assert.equal(guard.begin(), null)
  // A's finally settles -> idle again.
  guard.settle(a.generation, a.controller)
  assert.equal(guard.phase, 'idle')
  const b = guard.begin()
  assert.ok(b)
  assert.equal(guard.generation, 2)
})

test('a stale finally cannot settle or clear a newer invocation', () => {
  const guard = new InvocationGuard()
  const a = guard.begin()!
  guard.stop()
  guard.settle(a.generation, a.controller) // A's promise finally completes
  const b = guard.begin()!
  assert.ok(b, 'idle again after A settled')
  // A's LATE finally (defensive double-settle, or an async tail) must not
  // clear B's controller or claim B is idle.
  guard.settle(a.generation, a.controller)
  assert.equal(guard.phase, 'running', 'A must not clear B')
  assert.equal(b.controller.signal.aborted, false)
  // B is still stoppable.
  guard.stop()
  assert.equal(b.controller.signal.aborted, true)
  assert.equal(guard.phase, 'cancelling')
})

test('settle with the wrong controller is ignored', () => {
  const guard = new InvocationGuard()
  const a = guard.begin()!
  const foreign = new AbortController()
  guard.settle(a.generation, foreign)
  assert.equal(guard.phase, 'running')
  guard.settle(a.generation, a.controller)
  assert.equal(guard.phase, 'idle')
})

test('isCurrent tracks the live invocation only', () => {
  const guard = new InvocationGuard()
  const a = guard.begin()!
  assert.equal(guard.isCurrent(a.generation), true)
  guard.stop()
  assert.equal(guard.isCurrent(a.generation), true, 'cancelling is still the live invocation')
  guard.settle(a.generation, a.controller)
  assert.equal(guard.isCurrent(a.generation), false)
  const b = guard.begin()!
  assert.equal(guard.isCurrent(a.generation), false, 'old generation is stale')
  assert.equal(guard.isCurrent(b.generation), true)
})

test('invalidate aborts and permanently rejects callbacks from the old identity scope', () => {
  const guard = new InvocationGuard()
  const started = guard.begin()
  assert.ok(started)
  const oldGeneration = started.generation
  guard.invalidate()
  assert.equal(started.controller.signal.aborted, true)
  assert.equal(guard.phase, 'idle')
  assert.equal(guard.isCurrent(oldGeneration), false)
  const next = guard.begin()
  assert.ok(next)
  assert.notEqual(next.generation, oldGeneration)
})
