import {
  isValidChangePath,
  P6_CHANGESET_MAX_TEXT_BYTES,
  type FileVersion,
  type TaskOwnedChangeSet,
} from '@/lib/p6-changesets'

export const P6_CHANGE_JOURNAL_SCHEMA_VERSION = 1 as const
export const P6_CHANGE_JOURNAL_MAX_RECORDS = 40
export const P6_CHANGE_JOURNAL_MAX_BYTES = 4 * 1024 * 1024

export type P6ChangeJournalStatus = 'applied' | 'rolled_back' | 'conflict' | 'recovery_required'

export interface P6ChangeJournalRecord {
  readonly sessionId: string
  readonly changeSet: TaskOwnedChangeSet
  readonly status: P6ChangeJournalStatus
  readonly note: string
}

interface P6ChangeJournal {
  readonly schemaVersion: typeof P6_CHANGE_JOURNAL_SCHEMA_VERSION
  readonly records: readonly P6ChangeJournalRecord[]
}

const SHA256 = /^[0-9a-f]{64}$/u
const STATUS = new Set<P6ChangeJournalStatus>([
  'applied',
  'rolled_back',
  'conflict',
  'recovery_required',
])
const textEncoder = new TextEncoder()

export function p6ChangeJournalStorageKey(tenantId: string, workspaceId: string): string {
  return `omnibase.p6.changes.v1:${encodeURIComponent(tenantId)}:${encodeURIComponent(workspaceId)}`
}

function validVersion(value: unknown): value is FileVersion {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false
  const record = value as Record<string, unknown>
  if (record.kind === 'missing') return Object.keys(record).length === 1
  if (record.kind === 'binary')
    return (
      Object.keys(record).length === 2 &&
      typeof record.digest === 'string' &&
      SHA256.test(record.digest)
    )
  return (
    record.kind === 'text' &&
    Object.keys(record).length === 3 &&
    typeof record.content === 'string' &&
    !record.content.includes('\0') &&
    textEncoder.encode(record.content).byteLength <= P6_CHANGESET_MAX_TEXT_BYTES &&
    typeof record.digest === 'string' &&
    SHA256.test(record.digest)
  )
}

function validRecord(
  value: unknown,
  expectedScope?: { readonly tenantId: string; readonly workspaceId: string },
): value is P6ChangeJournalRecord {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false
  const record = value as Record<string, unknown>
  if (
    typeof record.sessionId !== 'string' ||
    record.sessionId.length === 0 ||
    record.sessionId.length > 200 ||
    typeof record.note !== 'string' ||
    record.note.length > 1_000 ||
    typeof record.status !== 'string' ||
    !STATUS.has(record.status as P6ChangeJournalStatus)
  )
    return false
  const changeSet = record.changeSet
  if (!changeSet || typeof changeSet !== 'object' || Array.isArray(changeSet)) return false
  const candidate = changeSet as Record<string, unknown>
  if (
    candidate.schemaVersion !== 1 ||
    ![
      candidate.id,
      candidate.tenantId,
      candidate.workspaceId,
      candidate.taskId,
      candidate.attemptId,
    ].every((item) => typeof item === 'string' && item.length > 0 && item.length <= 200) ||
    typeof candidate.createdAt !== 'string' ||
    candidate.createdAt.length > 64 ||
    !Number.isFinite(Date.parse(candidate.createdAt)) ||
    typeof candidate.manifestDigest !== 'string' ||
    !SHA256.test(candidate.manifestDigest) ||
    !Array.isArray(candidate.files) ||
    candidate.files.length === 0 ||
    candidate.files.length > 64 ||
    (expectedScope !== undefined &&
      (candidate.tenantId !== expectedScope.tenantId ||
        candidate.workspaceId !== expectedScope.workspaceId))
  )
    return false
  const paths = new Set<string>()
  for (const value of candidate.files) {
    if (!value || typeof value !== 'object' || Array.isArray(value)) return false
    const file = value as Record<string, unknown>
    if (
      Object.keys(file).length !== 3 ||
      typeof file.path !== 'string' ||
      !isValidChangePath(file.path) ||
      paths.has(file.path) ||
      !validVersion(file.before) ||
      !validVersion(file.after) ||
      JSON.stringify(file.before) === JSON.stringify(file.after)
    )
      return false
    paths.add(file.path)
  }
  return true
}

export function serializeP6ChangeJournal(records: readonly P6ChangeJournalRecord[]): string {
  const bounded = records
    .filter((record) => validRecord(record))
    .sort((left, right) => right.changeSet.createdAt.localeCompare(left.changeSet.createdAt))
    .slice(0, P6_CHANGE_JOURNAL_MAX_RECORDS)
  let journal: P6ChangeJournal = {
    schemaVersion: P6_CHANGE_JOURNAL_SCHEMA_VERSION,
    records: bounded,
  }
  while (
    new TextEncoder().encode(JSON.stringify(journal)).byteLength > P6_CHANGE_JOURNAL_MAX_BYTES
  ) {
    if (journal.records.length === 0) throw new Error('p6_change_journal_budget_exceeded')
    journal = { ...journal, records: journal.records.slice(0, -1) }
  }
  return JSON.stringify(journal)
}

export function parseP6ChangeJournal(
  raw: string | null,
  expectedScope?: { readonly tenantId: string; readonly workspaceId: string },
): readonly P6ChangeJournalRecord[] {
  if (!raw || new TextEncoder().encode(raw).byteLength > P6_CHANGE_JOURNAL_MAX_BYTES) return []
  try {
    const value: unknown = JSON.parse(raw)
    if (!value || typeof value !== 'object' || Array.isArray(value)) return []
    const journal = value as Record<string, unknown>
    if (
      journal.schemaVersion !== P6_CHANGE_JOURNAL_SCHEMA_VERSION ||
      !Array.isArray(journal.records) ||
      journal.records.length > P6_CHANGE_JOURNAL_MAX_RECORDS ||
      !journal.records.every((record) => validRecord(record, expectedScope))
    )
      return []
    const ids = new Set<string>()
    for (const record of journal.records) {
      const id = (record as P6ChangeJournalRecord).changeSet.id
      if (ids.has(id)) return []
      ids.add(id)
    }
    return journal.records as P6ChangeJournalRecord[]
  } catch {
    return []
  }
}
