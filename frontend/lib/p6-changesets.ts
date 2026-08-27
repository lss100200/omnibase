export const P6_CHANGESET_SCHEMA_VERSION = 1 as const
export const P6_CHANGESET_MAX_TEXT_BYTES = 1024 * 1024
export const P6_CHANGESET_MAX_MERGE_LINES = 2_000

export type DigestFunction = (bytes: Uint8Array) => Promise<string>

export interface ChangeSetOwner {
  readonly tenantId: string
  readonly workspaceId: string
  readonly taskId: string
  readonly attemptId: string
}

export type FileVersion =
  | { readonly kind: 'missing' }
  | { readonly kind: 'text'; readonly content: string; readonly digest: string }
  | { readonly kind: 'binary'; readonly digest: string }

export type FileVersionInput =
  | { readonly kind: 'missing' }
  | { readonly kind: 'text'; readonly content: string; readonly digest?: string }
  | { readonly kind: 'binary'; readonly digest: string }

export interface ChangeSetFile {
  readonly path: string
  /** B: exact task-start state, including pre-existing user dirty content. */
  readonly before: FileVersion
  /** A: exact Agent-produced state. */
  readonly after: FileVersion
}

export interface TaskOwnedChangeSet extends ChangeSetOwner {
  readonly schemaVersion: typeof P6_CHANGESET_SCHEMA_VERSION
  readonly id: string
  readonly createdAt: string
  readonly files: readonly ChangeSetFile[]
  readonly manifestDigest: string
}

export interface CurrentFile {
  readonly path: string
  /** C: current live-tree observation. */
  readonly version: FileVersion
}

export interface RollbackConflict {
  readonly path: string | null
  readonly code:
    | 'owner_mismatch'
    | 'invalid_path'
    | 'duplicate_path'
    | 'manifest_drift'
    | 'content_digest_drift'
    | 'current_file_missing'
    | 'binary_file'
    | 'overlapping_edits'
    | 'new_file_drift'
    | 'deleted_file_recreated'
    | 'merge_limit_exceeded'
  readonly detail: string
}

export interface RollbackOperation {
  readonly path: string
  readonly action: 'write' | 'delete' | 'noop'
  readonly expectedCurrentDigest: string | null
  readonly resultDigest: string | null
  readonly resultContent: string | null
  readonly reason: 'three_way' | 'exact_new_file' | 'exact_deleted_file' | 'already_rolled_back'
}

export interface RollbackReceipt extends ChangeSetOwner {
  readonly schemaVersion: typeof P6_CHANGESET_SCHEMA_VERSION
  readonly changeSetId: string
  readonly changeSetManifestDigest: string
  readonly state: 'ready' | 'already_applied' | 'conflict'
  readonly observedManifestDigest: string
  readonly plannedManifestDigest: string | null
  readonly planDigest: string | null
  readonly conflictCodes: readonly string[]
  readonly createdAt: string
  readonly receiptDigest: string
}

export type RollbackPreflightResult =
  | {
      readonly ok: true
      readonly state: 'ready' | 'already_applied'
      /** A compare-and-swap plan only. This module never writes live files. */
      readonly plan: readonly RollbackOperation[]
      readonly receipt: RollbackReceipt
    }
  | {
      readonly ok: false
      readonly state: 'conflict'
      readonly conflicts: readonly RollbackConflict[]
      readonly receipt: RollbackReceipt
    }

export interface CreateChangeSetInput extends ChangeSetOwner {
  readonly id: string
  readonly createdAt: string
  readonly files: readonly {
    readonly path: string
    readonly before: FileVersionInput
    readonly after: FileVersionInput
  }[]
}

export interface RollbackPreflightInput {
  readonly changeSet: TaskOwnedChangeSet
  readonly owner: ChangeSetOwner
  readonly currentFiles: readonly CurrentFile[]
  readonly createdAt: string
  readonly digest?: DigestFunction
}

const SHA256 = /^[0-9a-f]{64}$/u
const textEncoder = new TextEncoder()

function canonicalJson(value: unknown): string {
  if (value === null || typeof value !== 'object') return JSON.stringify(value)
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(',')}]`
  const record = value as Record<string, unknown>
  return `{${Object.keys(record)
    .sort()
    .map((key) => `${JSON.stringify(key)}:${canonicalJson(record[key])}`)
    .join(',')}}`
}

export const webCryptoSha256: DigestFunction = async (bytes) => {
  if (!globalThis.crypto?.subtle) throw new Error('p6_changeset_digest_unavailable')
  const result = await globalThis.crypto.subtle.digest('SHA-256', bytes)
  return [...new Uint8Array(result)].map((value) => value.toString(16).padStart(2, '0')).join('')
}

export async function sha256Text(
  value: string,
  digest: DigestFunction = webCryptoSha256,
): Promise<string> {
  const result = await digest(textEncoder.encode(value))
  if (!SHA256.test(result)) throw new Error('p6_changeset_digest_invalid')
  return result
}

export function isValidChangePath(path: string): boolean {
  if (
    path.length === 0 ||
    path.length > 1_024 ||
    path.includes('\0') ||
    path.includes('\\') ||
    path.startsWith('/') ||
    /^[A-Za-z]:/u.test(path)
  ) {
    return false
  }
  const parts = path.split('/')
  return parts.every(
    (part) =>
      part.length > 0 &&
      part !== '.' &&
      part !== '..' &&
      part.toLocaleLowerCase() !== '.git' &&
      !part.endsWith(' ') &&
      !part.endsWith('.'),
  )
}

async function materializeVersion(
  input: FileVersionInput,
  digest: DigestFunction,
): Promise<FileVersion> {
  if (input.kind !== 'text') {
    if (input.kind === 'binary' && !SHA256.test(input.digest)) {
      throw new Error('p6_changeset_digest_invalid')
    }
    return input
  }
  if (textEncoder.encode(input.content).byteLength > P6_CHANGESET_MAX_TEXT_BYTES) {
    throw new Error('p6_changeset_text_too_large')
  }
  if (input.content.includes('\0')) throw new Error('p6_changeset_binary_text')
  const actual = await sha256Text(input.content, digest)
  if (input.digest !== undefined && input.digest !== actual) {
    throw new Error('p6_changeset_content_digest_drift')
  }
  return { kind: 'text', content: input.content, digest: actual }
}

function changeSetPayload(changeSet: Omit<TaskOwnedChangeSet, 'manifestDigest'>): unknown {
  return {
    schemaVersion: changeSet.schemaVersion,
    id: changeSet.id,
    tenantId: changeSet.tenantId,
    workspaceId: changeSet.workspaceId,
    taskId: changeSet.taskId,
    attemptId: changeSet.attemptId,
    createdAt: changeSet.createdAt,
    files: changeSet.files,
  }
}

export async function createTaskOwnedChangeSet(
  input: CreateChangeSetInput,
  digest: DigestFunction = webCryptoSha256,
): Promise<TaskOwnedChangeSet> {
  for (const value of [
    input.id,
    input.tenantId,
    input.workspaceId,
    input.taskId,
    input.attemptId,
  ]) {
    if (!value.trim()) throw new Error('p6_changeset_owner_invalid')
  }
  if (input.files.length === 0) throw new Error('p6_changeset_files_empty')
  const paths = new Set<string>()
  const files: ChangeSetFile[] = []
  for (const file of input.files) {
    if (!isValidChangePath(file.path)) throw new Error('p6_changeset_path_invalid')
    if (paths.has(file.path)) throw new Error('p6_changeset_path_duplicate')
    paths.add(file.path)
    const before = await materializeVersion(file.before, digest)
    const after = await materializeVersion(file.after, digest)
    if (canonicalJson(before) === canonicalJson(after))
      throw new Error('p6_changeset_file_unchanged')
    files.push({ path: file.path, before, after })
  }
  files.sort((left, right) => left.path.localeCompare(right.path))
  const base = {
    schemaVersion: P6_CHANGESET_SCHEMA_VERSION,
    id: input.id,
    tenantId: input.tenantId,
    workspaceId: input.workspaceId,
    taskId: input.taskId,
    attemptId: input.attemptId,
    createdAt: input.createdAt,
    files,
  } as const
  return {
    ...base,
    manifestDigest: await sha256Text(canonicalJson(changeSetPayload(base)), digest),
  }
}

interface TextEdit {
  readonly start: number
  readonly end: number
  readonly replacement: readonly string[]
}

function splitLines(value: string): readonly string[] {
  if (value.length === 0) return []
  return value.match(/[^\n]*\n|[^\n]+$/gu) ?? []
}

function buildEdits(base: readonly string[], target: readonly string[]): readonly TextEdit[] {
  const rows: Uint32Array[] = Array.from(
    { length: base.length + 1 },
    () => new Uint32Array(target.length + 1),
  )
  for (let left = base.length - 1; left >= 0; left -= 1) {
    for (let right = target.length - 1; right >= 0; right -= 1) {
      rows[left]![right] =
        base[left] === target[right]
          ? rows[left + 1]![right + 1]! + 1
          : Math.max(rows[left + 1]![right]!, rows[left]![right + 1]!)
    }
  }

  const edits: TextEdit[] = []
  let left = 0
  let right = 0
  let pending: { start: number; end: number; replacement: string[] } | null = null
  const flush = () => {
    if (pending !== null) edits.push(pending)
    pending = null
  }
  while (left < base.length || right < target.length) {
    if (left < base.length && right < target.length && base[left] === target[right]) {
      flush()
      left += 1
      right += 1
    } else if (
      right < target.length &&
      (left === base.length || rows[left]![right + 1]! > rows[left + 1]![right]!)
    ) {
      pending ??= { start: left, end: left, replacement: [] }
      pending.replacement.push(target[right]!)
      right += 1
    } else {
      pending ??= { start: left, end: left, replacement: [] }
      pending.end += 1
      left += 1
    }
  }
  flush()
  return edits
}

function editsOverlap(left: TextEdit, right: TextEdit): boolean {
  const leftInsertion = left.start === left.end
  const rightInsertion = right.start === right.end
  if (leftInsertion && rightInsertion) return left.start === right.start
  if (leftInsertion) return left.start >= right.start && left.start <= right.end
  if (rightInsertion) return right.start >= left.start && right.start <= left.end
  return Math.max(left.start, right.start) < Math.min(left.end, right.end)
}

function mergeNonOverlapping(
  baseText: string,
  rollbackText: string,
  currentText: string,
): string | null {
  const base = splitLines(baseText)
  const rollback = splitLines(rollbackText)
  const current = splitLines(currentText)
  if (
    base.length > P6_CHANGESET_MAX_MERGE_LINES ||
    rollback.length > P6_CHANGESET_MAX_MERGE_LINES ||
    current.length > P6_CHANGESET_MAX_MERGE_LINES
  ) {
    throw new Error('merge_limit_exceeded')
  }
  const rollbackEdits = buildEdits(base, rollback)
  const userEdits = buildEdits(base, current)
  if (rollbackEdits.some((left) => userEdits.some((right) => editsOverlap(left, right))))
    return null

  const edits = [...rollbackEdits, ...userEdits].sort(
    (left, right) => left.start - right.start || left.end - right.end,
  )
  const result: string[] = []
  let cursor = 0
  for (const edit of edits) {
    result.push(...base.slice(cursor, edit.start), ...edit.replacement)
    cursor = edit.end
  }
  result.push(...base.slice(cursor))
  return result.join('')
}

function ownerMatches(left: ChangeSetOwner, right: ChangeSetOwner): boolean {
  return (
    left.tenantId === right.tenantId &&
    left.workspaceId === right.workspaceId &&
    left.taskId === right.taskId &&
    left.attemptId === right.attemptId
  )
}

async function validateVersion(
  version: FileVersion,
  digest: DigestFunction,
): Promise<'ok' | 'binary' | 'drift'> {
  if (version.kind === 'missing') return 'ok'
  if (!SHA256.test(version.digest)) return 'drift'
  if (version.kind === 'binary') return 'binary'
  if (version.content.includes('\0')) return 'binary'
  return (await sha256Text(version.content, digest)) === version.digest ? 'ok' : 'drift'
}

function manifestPayload(files: readonly { path: string; version: FileVersion }[]): unknown {
  return files
    .map(({ path, version }) => ({
      path,
      kind: version.kind,
      digest: version.kind === 'missing' ? null : version.digest,
    }))
    .sort((left, right) => left.path.localeCompare(right.path))
}

async function manifestDigest(
  files: readonly { path: string; version: FileVersion }[],
  digest: DigestFunction,
): Promise<string> {
  return sha256Text(canonicalJson(manifestPayload(files)), digest)
}

async function makeReceipt(
  input: Omit<RollbackReceipt, 'receiptDigest'>,
  digest: DigestFunction,
): Promise<RollbackReceipt> {
  return { ...input, receiptDigest: await sha256Text(canonicalJson(input), digest) }
}

export async function verifyRollbackReceipt(
  receipt: RollbackReceipt,
  digest: DigestFunction = webCryptoSha256,
): Promise<boolean> {
  const { receiptDigest, ...payload } = receipt
  return (
    SHA256.test(receiptDigest) &&
    (await sha256Text(canonicalJson(payload), digest)) === receiptDigest
  )
}

export async function preflightTaskChangeSetRollback(
  input: RollbackPreflightInput,
): Promise<RollbackPreflightResult> {
  const digest = input.digest ?? webCryptoSha256
  const conflicts: RollbackConflict[] = []
  const changeSet = input.changeSet
  if (!ownerMatches(changeSet, input.owner)) {
    conflicts.push({
      path: null,
      code: 'owner_mismatch',
      detail: 'ChangeSet owner binding differs.',
    })
  }
  const recomputedManifest = await sha256Text(canonicalJson(changeSetPayload(changeSet)), digest)
  if (recomputedManifest !== changeSet.manifestDigest) {
    conflicts.push({ path: null, code: 'manifest_drift', detail: 'ChangeSet manifest drifted.' })
  }

  const changePaths = new Set<string>()
  for (const file of changeSet.files) {
    if (!isValidChangePath(file.path)) {
      conflicts.push({
        path: file.path,
        code: 'invalid_path',
        detail: 'Path is outside the closed relative-path profile.',
      })
    } else if (changePaths.has(file.path)) {
      conflicts.push({
        path: file.path,
        code: 'duplicate_path',
        detail: 'ChangeSet path is duplicated.',
      })
    }
    changePaths.add(file.path)
    for (const version of [file.before, file.after]) {
      const validity = await validateVersion(version, digest)
      if (validity === 'binary') {
        conflicts.push({
          path: file.path,
          code: 'binary_file',
          detail: 'Binary content is not mergeable.',
        })
      } else if (validity === 'drift') {
        conflicts.push({
          path: file.path,
          code: 'content_digest_drift',
          detail: 'Stored ChangeSet content does not match its digest.',
        })
      }
    }
  }

  const currentByPath = new Map<string, FileVersion>()
  for (const current of input.currentFiles) {
    if (!isValidChangePath(current.path)) {
      conflicts.push({
        path: current.path,
        code: 'invalid_path',
        detail: 'Current path is invalid.',
      })
    } else if (currentByPath.has(current.path)) {
      conflicts.push({
        path: current.path,
        code: 'duplicate_path',
        detail: 'Current path is duplicated.',
      })
    }
    currentByPath.set(current.path, current.version)
  }

  const observed: { path: string; version: FileVersion }[] = []
  for (const file of changeSet.files) {
    const current = currentByPath.get(file.path)
    if (current === undefined) {
      conflicts.push({
        path: file.path,
        code: 'current_file_missing',
        detail: 'Every task path requires an explicit current observation.',
      })
      continue
    }
    observed.push({ path: file.path, version: current })
    const validity = await validateVersion(current, digest)
    if (validity === 'binary') {
      conflicts.push({
        path: file.path,
        code: 'binary_file',
        detail: 'Current binary content is not mergeable.',
      })
    } else if (validity === 'drift') {
      conflicts.push({
        path: file.path,
        code: 'content_digest_drift',
        detail: 'Current content does not match its supplied digest.',
      })
    }
  }
  const observedManifestDigest = await manifestDigest(observed, digest)

  const operations: RollbackOperation[] = []
  if (conflicts.length === 0) {
    for (const file of changeSet.files) {
      const current = currentByPath.get(file.path)!
      const currentDigest = current.kind === 'missing' ? null : current.digest
      if (canonicalJson(current) === canonicalJson(file.before)) {
        operations.push({
          path: file.path,
          action: 'noop',
          expectedCurrentDigest: currentDigest,
          resultDigest: currentDigest,
          resultContent: current.kind === 'text' ? current.content : null,
          reason: 'already_rolled_back',
        })
        continue
      }
      if (file.before.kind === 'missing') {
        if (
          file.after.kind !== 'text' ||
          current.kind !== 'text' ||
          current.digest !== file.after.digest
        ) {
          conflicts.push({
            path: file.path,
            code: 'new_file_drift',
            detail: 'Agent-created file changed after the task.',
          })
        } else {
          operations.push({
            path: file.path,
            action: 'delete',
            expectedCurrentDigest: current.digest,
            resultDigest: null,
            resultContent: null,
            reason: 'exact_new_file',
          })
        }
        continue
      }
      if (file.after.kind === 'missing') {
        if (current.kind !== 'missing' || file.before.kind !== 'text') {
          conflicts.push({
            path: file.path,
            code: 'deleted_file_recreated',
            detail: 'Agent-deleted path was recreated with non-baseline content.',
          })
        } else {
          operations.push({
            path: file.path,
            action: 'write',
            expectedCurrentDigest: null,
            resultDigest: file.before.digest,
            resultContent: file.before.content,
            reason: 'exact_deleted_file',
          })
        }
        continue
      }
      if (file.before.kind !== 'text' || file.after.kind !== 'text' || current.kind !== 'text') {
        conflicts.push({
          path: file.path,
          code: 'binary_file',
          detail: 'Only text-to-text changes support three-way rollback.',
        })
        continue
      }
      try {
        const merged = mergeNonOverlapping(file.after.content, file.before.content, current.content)
        if (merged === null) {
          conflicts.push({
            path: file.path,
            code: 'overlapping_edits',
            detail: 'User and rollback edits overlap.',
          })
          continue
        }
        const resultDigest = await sha256Text(merged, digest)
        operations.push({
          path: file.path,
          action: merged === current.content ? 'noop' : 'write',
          expectedCurrentDigest: current.digest,
          resultDigest,
          resultContent: merged,
          reason: 'three_way',
        })
      } catch (error) {
        if (error instanceof Error && error.message === 'merge_limit_exceeded') {
          conflicts.push({
            path: file.path,
            code: 'merge_limit_exceeded',
            detail: 'Text exceeds the bounded merge profile.',
          })
        } else {
          throw error
        }
      }
    }
  }

  const conflictCodes = conflicts.map((conflict) => conflict.code).sort()
  if (conflicts.length > 0) {
    const receipt = await makeReceipt(
      {
        schemaVersion: P6_CHANGESET_SCHEMA_VERSION,
        changeSetId: changeSet.id,
        tenantId: input.owner.tenantId,
        workspaceId: input.owner.workspaceId,
        taskId: input.owner.taskId,
        attemptId: input.owner.attemptId,
        changeSetManifestDigest: changeSet.manifestDigest,
        state: 'conflict',
        observedManifestDigest,
        plannedManifestDigest: null,
        planDigest: null,
        conflictCodes,
        createdAt: input.createdAt,
      },
      digest,
    )
    return { ok: false, state: 'conflict', conflicts, receipt }
  }

  const resultVersions = operations.map((operation) => ({
    path: operation.path,
    version:
      operation.resultDigest === null
        ? ({ kind: 'missing' } as const)
        : ({
            kind: 'text',
            content: operation.resultContent!,
            digest: operation.resultDigest,
          } as const),
  }))
  const plannedManifestDigest = await manifestDigest(resultVersions, digest)
  const planDigest = await sha256Text(canonicalJson(operations), digest)
  const state = operations.every((operation) => operation.action === 'noop')
    ? 'already_applied'
    : 'ready'
  const receipt = await makeReceipt(
    {
      schemaVersion: P6_CHANGESET_SCHEMA_VERSION,
      changeSetId: changeSet.id,
      tenantId: input.owner.tenantId,
      workspaceId: input.owner.workspaceId,
      taskId: input.owner.taskId,
      attemptId: input.owner.attemptId,
      changeSetManifestDigest: changeSet.manifestDigest,
      state,
      observedManifestDigest,
      plannedManifestDigest,
      planDigest,
      conflictCodes: [],
      createdAt: input.createdAt,
    },
    digest,
  )
  return { ok: true, state, plan: operations, receipt }
}
