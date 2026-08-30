import assert from 'node:assert/strict'
import { test } from 'node:test'

import { p7SettingsNavigationTargetIndex } from './p7-settings-accessibility'

test('settings roving navigation wraps and supports Home and End', () => {
  assert.equal(p7SettingsNavigationTargetIndex(0, 20, 'ArrowDown'), 1)
  assert.equal(p7SettingsNavigationTargetIndex(19, 20, 'ArrowDown'), 0)
  assert.equal(p7SettingsNavigationTargetIndex(0, 20, 'ArrowUp'), 19)
  assert.equal(p7SettingsNavigationTargetIndex(7, 20, 'Home'), 0)
  assert.equal(p7SettingsNavigationTargetIndex(7, 20, 'End'), 19)
})

test('settings roving navigation fails closed for an empty or invalid collection', () => {
  assert.equal(p7SettingsNavigationTargetIndex(0, 0, 'ArrowDown'), null)
  assert.equal(p7SettingsNavigationTargetIndex(Number.NaN, 20, 'ArrowDown'), null)
})
