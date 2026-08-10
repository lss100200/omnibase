import assert from 'node:assert/strict'
import { test } from 'node:test'

import { isUserCancelledError } from './cancel-detection'

test('fetch AbortError is a user cancellation', () => {
  assert.equal(isUserCancelledError(new DOMException('aborted', 'AbortError')), true)
})

test('backend SSE cancelled event (no code) is a user cancellation', () => {
  // The backend cancelled payload has no `code`; the client raises
  // Error("cancelled") — both spellings must map to the friendly message.
  assert.equal(isUserCancelledError(new Error('cancelled')), true)
  assert.equal(isUserCancelledError(new Error('agent_alpha_cancelled')), true)
})

test('other errors are not user cancellations', () => {
  assert.equal(isUserCancelledError(new Error('agent_alpha_unavailable')), false)
  assert.equal(isUserCancelledError(new Error('agent_alpha_binding_not_found')), false)
  assert.equal(isUserCancelledError(new Error('BodyStreamBuffer was aborted')), false)
  assert.equal(isUserCancelledError('cancelled'), false)
  assert.equal(isUserCancelledError(null), false)
  assert.equal(isUserCancelledError(undefined), false)
})
