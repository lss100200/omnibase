import assert from 'node:assert/strict'
import test from 'node:test'
import {
  createTaskOwnedChangeSet,
  isValidChangePath,
  preflightTaskChangeSetRollback,
  sha256Text,
  verifyRollbackReceipt,
  type ChangeSetOwner,
  type CurrentFile,
  type FileVersionInput,
} from './p6-changesets'

const owner: ChangeSetOwner = {
  tenantId: 'tenant-1',
  workspaceId: 'workspace-1',
  taskId: 'task-1',
  attemptId: 'attempt-1',
}

async function changeSet(
  before: FileVersionInput,
  after: FileVersionInput,
  path = 'src/example.ts',
) {
  return createTaskOwnedChangeSet({
    ...owner,
    id: 'changeset-1',
    createdAt: '2026-08-13T01:00:00.000Z',
    files: [{ path, before, after }],
  })
}

async function text(content: string) {
  return { kind: 'text' as const, content, digest: await sha256Text(content) }
}

async function preflight(
  set: Awaited<ReturnType<typeof changeSet>>,
  currentFiles: readonly CurrentFile[],
) {
  return preflightTaskChangeSetRollback({
    changeSet: set,
    owner,
    currentFiles,
    createdAt: '2026-08-13T02:00:00.000Z',
  })
}

test('relative paths are closed against traversal, absolute paths and Git internals', () => {
  assert.equal(isValidChangePath('src/nested/file.ts'), true)
  for (const path of [
    '../secret',
    'src/../../secret',
    '/etc/passwd',
    'C:/Users/owner/.env',
    'src\\file.ts',
    '.git/config',
    'src/.GIT/index',
    'src//file.ts',
  ]) {
    assert.equal(isValidChangePath(path), false, path)
  }
})

test('non-overlapping user edits survive rollback and pre-task dirty baseline is restored', async () => {
  const before = 'user dirty header\nagent target: old\nuser tail: old\n'
  const after = 'user dirty header\nagent target: new\nuser tail: old\n'
  const current = 'user dirty header\nagent target: new\nuser tail: edited later\n'
  const result = await preflight(
    await changeSet({ kind: 'text', content: before }, { kind: 'text', content: after }),
    [{ path: 'src/example.ts', version: await text(current) }],
  )
  assert.equal(result.ok, true)
  if (!result.ok) return
  assert.equal(result.state, 'ready')
  assert.equal(
    result.plan[0]?.resultContent,
    'user dirty header\nagent target: old\nuser tail: edited later\n',
  )
  assert.equal(await verifyRollbackReceipt(result.receipt), true)
})

test('overlapping user and Agent edits fail closed without a partial plan', async () => {
  const set = await changeSet(
    { kind: 'text', content: 'alpha\ntarget before\nomega\n' },
    { kind: 'text', content: 'alpha\ntarget after\nomega\n' },
  )
  const result = await preflight(set, [
    { path: 'src/example.ts', version: await text('alpha\nuser changed target\nomega\n') },
  ])
  assert.equal(result.ok, false)
  if (result.ok) return
  assert.deepEqual(
    result.conflicts.map((conflict) => conflict.code),
    ['overlapping_edits'],
  )
  assert.equal(result.receipt.planDigest, null)
})

test('Agent-created files are deleted only on an exact current digest', async () => {
  const set = await changeSet({ kind: 'missing' }, { kind: 'text', content: 'created\n' })
  const exact = await preflight(set, [{ path: 'src/example.ts', version: await text('created\n') }])
  assert.equal(exact.ok, true)
  if (exact.ok) assert.equal(exact.plan[0]?.action, 'delete')

  const drift = await preflight(set, [
    { path: 'src/example.ts', version: await text('user edited\n') },
  ])
  assert.equal(drift.ok, false)
  if (!drift.ok) assert.equal(drift.conflicts[0]?.code, 'new_file_drift')
})

test('Agent-deleted files are restored only while still absent', async () => {
  const set = await changeSet({ kind: 'text', content: 'original\n' }, { kind: 'missing' })
  const exact = await preflight(set, [{ path: 'src/example.ts', version: { kind: 'missing' } }])
  assert.equal(exact.ok, true)
  if (exact.ok) {
    assert.equal(exact.plan[0]?.action, 'write')
    assert.equal(exact.plan[0]?.resultContent, 'original\n')
  }

  const recreated = await preflight(set, [
    { path: 'src/example.ts', version: await text('different recreation\n') },
  ])
  assert.equal(recreated.ok, false)
  if (!recreated.ok) assert.equal(recreated.conflicts[0]?.code, 'deleted_file_recreated')
})

test('a repeated rollback is a verified no-op receipt', async () => {
  const before = 'before\n'
  const set = await changeSet(
    { kind: 'text', content: before },
    { kind: 'text', content: 'after\n' },
  )
  const result = await preflight(set, [{ path: 'src/example.ts', version: await text(before) }])
  assert.equal(result.ok, true)
  if (!result.ok) return
  assert.equal(result.state, 'already_applied')
  assert.equal(result.plan[0]?.action, 'noop')
  assert.equal(await verifyRollbackReceipt(result.receipt), true)
})

test('stored, current and manifest digest drift all fail closed', async () => {
  const set = await changeSet(
    { kind: 'text', content: 'before\n' },
    { kind: 'text', content: 'after\n' },
  )
  const currentDrift = await preflight(set, [
    {
      path: 'src/example.ts',
      version: {
        kind: 'text',
        content: 'after but tampered\n',
        digest: await sha256Text('after\n'),
      },
    },
  ])
  assert.equal(currentDrift.ok, false)
  if (!currentDrift.ok) {
    assert.equal(
      currentDrift.conflicts.some((conflict) => conflict.code === 'content_digest_drift'),
      true,
    )
  }

  const manifestDrift = await preflight({ ...set, taskId: 'tampered-task' }, [
    { path: 'src/example.ts', version: await text('after\n') },
  ])
  assert.equal(manifestDrift.ok, false)
  if (!manifestDrift.ok) {
    assert.equal(
      manifestDrift.conflicts.some((conflict) => conflict.code === 'manifest_drift'),
      true,
    )
    assert.equal(
      manifestDrift.conflicts.some((conflict) => conflict.code === 'owner_mismatch'),
      true,
    )
  }
})

test('binary content and task-owner substitution fail closed', async () => {
  const binaryDigest = await sha256Text('binary-placeholder')
  const set = await changeSet(
    { kind: 'binary', digest: binaryDigest },
    { kind: 'text', content: 'text replacement\n' },
  )
  const binary = await preflight(set, [
    { path: 'src/example.ts', version: await text('text replacement\n') },
  ])
  assert.equal(binary.ok, false)
  if (!binary.ok)
    assert.equal(
      binary.conflicts.some((conflict) => conflict.code === 'binary_file'),
      true,
    )

  const textSet = await changeSet(
    { kind: 'text', content: 'before\n' },
    { kind: 'text', content: 'after\n' },
  )
  const wrongOwner = await preflightTaskChangeSetRollback({
    changeSet: textSet,
    owner: { ...owner, attemptId: 'other-attempt' },
    currentFiles: [{ path: 'src/example.ts', version: await text('after\n') }],
    createdAt: '2026-08-13T02:00:00.000Z',
  })
  assert.equal(wrongOwner.ok, false)
  if (!wrongOwner.ok) assert.equal(wrongOwner.conflicts[0]?.code, 'owner_mismatch')
})
