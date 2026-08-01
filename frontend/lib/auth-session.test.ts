import assert from 'node:assert/strict'
import test from 'node:test'
import { classifyAuthFailure, getSafeReturnPath } from './auth-session'

test('auth failure classification invalidates only explicit client auth failures', () => {
  assert.equal(classifyAuthFailure(400), 'invalid')
  assert.equal(classifyAuthFailure(401), 'invalid')
  assert.equal(classifyAuthFailure(403), 'invalid')
  assert.equal(classifyAuthFailure(500), 'transient')
  assert.equal(classifyAuthFailure(503), 'transient')
  assert.equal(classifyAuthFailure(undefined), 'transient')
})

test('safe return path accepts only same-origin absolute paths', () => {
  assert.equal(getSafeReturnPath('/knowledge?view=recent#item'), '/knowledge?view=recent#item')
  assert.equal(getSafeReturnPath('/'), '/')
  assert.equal(getSafeReturnPath('https://evil.example/phish'), '/dashboard')
  assert.equal(getSafeReturnPath('//evil.example/phish'), '/dashboard')
  assert.equal(getSafeReturnPath('dashboard'), '/dashboard')
  assert.equal(getSafeReturnPath(null), '/dashboard')
})
