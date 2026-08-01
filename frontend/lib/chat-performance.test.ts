import assert from 'node:assert/strict'
import test from 'node:test'
import { createTrailingThrottle, isNearBottom } from './chat-performance'

const delay = (milliseconds: number) =>
  new Promise<void>((resolve) => setTimeout(resolve, milliseconds))

test('near-bottom detection uses the remaining scroll distance', () => {
  assert.equal(isNearBottom({ scrollTop: 804, scrollHeight: 1000, clientHeight: 100 }, 96), true)
  assert.equal(isNearBottom({ scrollTop: 803, scrollHeight: 1000, clientHeight: 100 }, 96), false)
})

test('trailing throttle coalesces chunk renders and keeps the latest value', async () => {
  const rendered: string[] = []
  const throttle = createTrailingThrottle<string>(35, (value) => rendered.push(value))

  throttle.push('a')
  throttle.push('ab')
  throttle.push('abc')

  assert.deepEqual(rendered, [])
  await delay(55)
  assert.deepEqual(rendered, ['abc'])
})

test('flush publishes pending content immediately and prevents a later duplicate', async () => {
  const rendered: string[] = []
  const throttle = createTrailingThrottle<string>(40, (value) => rendered.push(value))

  throttle.push('final chunk')
  throttle.flush()

  assert.deepEqual(rendered, ['final chunk'])
  await delay(60)
  assert.deepEqual(rendered, ['final chunk'])
})

test('cancel drops pending content and is safe after a flush', async () => {
  const rendered: string[] = []
  const throttle = createTrailingThrottle<string>(35, (value) => rendered.push(value))

  throttle.push('stale chunk')
  throttle.cancel()
  throttle.flush()

  await delay(55)
  assert.deepEqual(rendered, [])
})
