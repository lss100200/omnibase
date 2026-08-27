import assert from 'node:assert/strict'
import test from 'node:test'
import { createTaskOwnedChangeSet } from './p6-changesets'
import {
  parseP6ChangeJournal,
  p6ChangeJournalStorageKey,
  serializeP6ChangeJournal,
} from './p6-change-journal'

const fakeDigest = async () => 'a'.repeat(64)

test('ChangeSet journal round-trips scoped local recovery records', async () => {
  const changeSet = await createTaskOwnedChangeSet(
    {
      id: 'change-1',
      tenantId: 'tenant',
      workspaceId: 'workspace',
      taskId: 'task',
      attemptId: 'attempt',
      createdAt: '2026-08-14T00:00:00.000Z',
      files: [
        {
          path: 'README.md',
          before: { kind: 'text', content: 'before' },
          after: { kind: 'text', content: 'after' },
        },
      ],
    },
    fakeDigest,
  )
  const records = [
    { sessionId: 'session', changeSet, status: 'applied' as const, note: 'reviewed' },
  ]
  assert.deepEqual(parseP6ChangeJournal(serializeP6ChangeJournal(records)), records)
  assert.notEqual(
    p6ChangeJournalStorageKey('tenant-a', 'workspace'),
    p6ChangeJournalStorageKey('tenant-b', 'workspace'),
  )
})

test('ChangeSet journal fails closed on malformed, duplicate or oversized projections', async () => {
  assert.deepEqual(parseP6ChangeJournal('{broken'), [])
  assert.deepEqual(parseP6ChangeJournal(JSON.stringify({ schemaVersion: 2, records: [] })), [])
  const changeSet = await createTaskOwnedChangeSet(
    {
      id: 'change-1',
      tenantId: 'tenant',
      workspaceId: 'workspace',
      taskId: 'task',
      attemptId: 'attempt',
      createdAt: '2026-08-14T00:00:00.000Z',
      files: [
        {
          path: 'README.md',
          before: { kind: 'text', content: 'before' },
          after: { kind: 'text', content: 'after' },
        },
      ],
    },
    fakeDigest,
  )
  const item = { sessionId: 'session', changeSet, status: 'applied', note: 'reviewed' }
  assert.deepEqual(
    parseP6ChangeJournal(JSON.stringify({ schemaVersion: 1, records: [item, item] })),
    [],
  )
  assert.deepEqual(
    parseP6ChangeJournal(JSON.stringify({ schemaVersion: 1, records: [item] }), {
      tenantId: 'another-tenant',
      workspaceId: 'workspace',
    }),
    [],
  )
  const malformedFile = {
    ...item,
    changeSet: {
      ...item.changeSet,
      files: [{ ...item.changeSet.files[0], path: '../outside.txt' }],
    },
  }
  assert.deepEqual(
    parseP6ChangeJournal(JSON.stringify({ schemaVersion: 1, records: [malformedFile] })),
    [],
  )
})
