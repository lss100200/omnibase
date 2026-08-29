import assert from 'node:assert/strict'
import { test } from 'node:test'

import {
  beginP7WorkspaceDirectoryList,
  beginP7WorkspaceFileAuthorization,
  beginP7WorkspaceFileRead,
  createP7WorkspaceFilesState,
  failP7WorkspaceFileRead,
  isP7WorkspaceLogicalPath,
  p7WorkspaceFileDirectory,
  releaseP7WorkspaceFilesAuthorization,
  settleP7WorkspaceDirectoryList,
  settleP7WorkspaceFileAuthorization,
  settleP7WorkspaceFileRead,
  switchP7WorkspaceFilesWorkspace,
} from './p7-workspace-files'

const WORKSPACE_A = `workspace_${'a'.repeat(32)}`
const WORKSPACE_B = `workspace_${'b'.repeat(32)}`

function authorize(workspaceId = WORKSPACE_A, generation = 1) {
  const started = beginP7WorkspaceFileAuthorization(createP7WorkspaceFilesState(workspaceId))!
  return settleP7WorkspaceFileAuthorization(started.state, started.request, {
    workspaceId,
    rootName: 'project',
    authorizationGeneration: generation,
  })
}

test('logical paths accept only normalized relative paths and the explicit root', () => {
  assert.equal(isP7WorkspaceLogicalPath('', true), true)
  assert.equal(isP7WorkspaceLogicalPath('', false), false)
  assert.equal(isP7WorkspaceLogicalPath('src/main.ts', false), true)
  for (const value of [
    '/etc/passwd',
    'C:/owner/file.ts',
    '..',
    'src/../file.ts',
    'src\\file.ts',
    'src//file.ts',
    'src/file.ts/',
    'src/file. ',
    'src/CON.txt',
    'src/%2e%2e/file.ts',
    `src/${'part/'.repeat(32)}file.ts`,
    'src/ＦＩＬＥ.ts',
  ]) {
    assert.equal(isP7WorkspaceLogicalPath(value, true), false, value)
  }
})

test('authorization DTO is closed and bound to the requested workspace', () => {
  const started = beginP7WorkspaceFileAuthorization(createP7WorkspaceFilesState(WORKSPACE_A))!
  const extra = settleP7WorkspaceFileAuthorization(started.state, started.request, {
    workspaceId: WORKSPACE_A,
    rootName: 'project',
    authorizationGeneration: 1,
    absolutePath: 'C:/owner/project',
  })
  assert.equal(extra.phase, 'error')
  assert.equal(extra.errorCode, 'desktop_native_response_invalid')

  const wrongWorkspace = settleP7WorkspaceFileAuthorization(started.state, started.request, {
    workspaceId: WORKSPACE_B,
    rootName: 'project',
    authorizationGeneration: 1,
  })
  assert.equal(wrongWorkspace.phase, 'error')
  assert.equal(wrongWorkspace.authorization, null)
})

test('lazy list accepts exact immediate children and rejects malformed closed DTOs', () => {
  const state = authorize()
  const started = beginP7WorkspaceDirectoryList(state, '')!
  const listed = settleP7WorkspaceDirectoryList(started.state, started.request, {
    directoryPath: '',
    entries: [
      {
        path: 'src',
        name: 'src',
        kind: 'directory',
        sizeBytes: null,
        lastModifiedMs: 10,
      },
      {
        path: 'README.md',
        name: 'README.md',
        kind: 'file',
        sizeBytes: 12,
        lastModifiedMs: 11,
      },
    ],
    truncated: false,
  })
  assert.equal(p7WorkspaceFileDirectory(listed, '')?.status, 'ready')
  assert.equal(p7WorkspaceFileDirectory(listed, '')?.entries.length, 2)

  const malformedStart = beginP7WorkspaceDirectoryList(state, '')!
  const malformed = settleP7WorkspaceDirectoryList(malformedStart.state, malformedStart.request, {
    directoryPath: '',
    entries: [
      {
        path: 'src/nested.ts',
        name: 'nested.ts',
        kind: 'file',
        sizeBytes: 1,
        lastModifiedMs: 1,
      },
    ],
    truncated: false,
  })
  assert.equal(malformed.phase, 'error')
  assert.equal(malformed.authorization, null)
  assert.deepEqual(malformed.directories, [])
  assert.equal(malformed.errorCode, 'desktop_native_response_invalid')
})

test('read admits bounded exact UTF-8 content with a lowercase SHA-256 only', () => {
  const state = authorize()
  const started = beginP7WorkspaceFileRead(state, 'src/main.ts')!
  const content = 'export const ready = true\n'
  const read = settleP7WorkspaceFileRead(started.state, started.request, {
    path: 'src/main.ts',
    content,
    sizeBytes: new TextEncoder().encode(content).byteLength,
    lastModifiedMs: 12,
    sha256: 'a'.repeat(64),
  })
  assert.equal(read.readPhase, 'ready')
  assert.equal(read.openFile?.content, content)

  const invalidStart = beginP7WorkspaceFileRead(state, 'src/main.ts')!
  const invalid = settleP7WorkspaceFileRead(invalidStart.state, invalidStart.request, {
    path: 'src/main.ts',
    content,
    sizeBytes: 1,
    lastModifiedMs: 12,
    sha256: 'A'.repeat(64),
  })
  assert.equal(invalid.phase, 'error')
  assert.equal(invalid.authorization, null)
  assert.equal(invalid.openFile, null)
  assert.equal(invalid.errorCode, 'desktop_native_response_invalid')
})

test('workspace switch rejects late list and read responses from workspace A', () => {
  const authorizedA = authorize(WORKSPACE_A, 7)
  const listA = beginP7WorkspaceDirectoryList(authorizedA, '')!
  const readA = beginP7WorkspaceFileRead(listA.state, 'main.ts')!

  const switched = switchP7WorkspaceFilesWorkspace(readA.state, WORKSPACE_B)
  const authorizeB = beginP7WorkspaceFileAuthorization(switched)!
  const authorizedB = settleP7WorkspaceFileAuthorization(authorizeB.state, authorizeB.request, {
    workspaceId: WORKSPACE_B,
    rootName: 'other',
    authorizationGeneration: 8,
  })

  const lateList = settleP7WorkspaceDirectoryList(authorizedB, listA.request, {
    directoryPath: '',
    entries: [],
    truncated: false,
  })
  assert.equal(lateList, authorizedB)
  const lateRead = settleP7WorkspaceFileRead(lateList, readA.request, {
    path: 'main.ts',
    content: 'leak',
    sizeBytes: 4,
    lastModifiedMs: 1,
    sha256: 'b'.repeat(64),
  })
  assert.equal(lateRead, authorizedB)
  assert.equal(lateRead.workspaceId, WORKSPACE_B)
  assert.equal(lateRead.openFile, null)
})

test('release clears the tree and buffer and invalidates pending responses', () => {
  const authorized = authorize(WORKSPACE_A, 3)
  const list = beginP7WorkspaceDirectoryList(authorized, '')!
  const read = beginP7WorkspaceFileRead(list.state, 'main.ts')!
  const released = releaseP7WorkspaceFilesAuthorization(read.state).state
  assert.equal(released.authorization, null)
  assert.deepEqual(released.directories, [])
  assert.equal(released.openFile, null)

  const late = settleP7WorkspaceFileRead(released, read.request, {
    path: 'main.ts',
    content: 'late',
    sizeBytes: 4,
    lastModifiedMs: 1,
    sha256: 'c'.repeat(64),
  })
  assert.equal(late, released)
})

test('native generation loss clears projected data and returns to reselect state', () => {
  const authorized = authorize(WORKSPACE_A, 9)
  const read = beginP7WorkspaceFileRead(authorized, 'main.ts')!
  const invalidated = failP7WorkspaceFileRead(
    read.state,
    read.request,
    'desktop_workspace_files_generation_conflict',
  )
  assert.equal(invalidated.phase, 'error')
  assert.equal(invalidated.authorization, null)
  assert.deepEqual(invalidated.directories, [])
  assert.equal(invalidated.selectedPath, null)
})

test('native identity drift clears projected data and returns to reselect state', () => {
  const authorized = authorize(WORKSPACE_A, 10)
  const read = beginP7WorkspaceFileRead(authorized, 'main.ts')!
  const invalidated = failP7WorkspaceFileRead(
    read.state,
    read.request,
    'desktop_workspace_files_identity_drift',
  )
  assert.equal(invalidated.phase, 'error')
  assert.equal(invalidated.authorization, null)
  assert.deepEqual(invalidated.directories, [])
  assert.equal(invalidated.selectedPath, null)
})
