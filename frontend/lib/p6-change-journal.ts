import type { TaskOwnedChangeSet } from '@/lib/p6-changesets'

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

export function p6ChangeJournalStorageKey(tenantId: string, workspaceId: string): string {
  return `omnibase.p6.changes.v1:${encodeURIComponent(tenantId)}:${encodeURIComponent(workspaceId)}`
}

function validRecord(value: unknown): value is P6ChangeJournalRecord {
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
  return (
    candidate.schemaVersion === 1 &&
    typeof candidate.id === 'string' &&
    typeof candidate.tenantId === 'string' &&
    typeof candidate.workspaceId === 'string' &&
    typeof candidate.taskId === 'string' &&
    typeof candidate.attemptId === 'string' &&
    typeof candidate.createdAt === 'string' &&
    typeof candidate.manifestDigest === 'string' &&
    SHA256.test(candidate.manifestDigest) &&
    Array.isArray(candidate.files) &&
    candidate.files.length > 0 &&
    candidate.files.length <= 64
  )
}

export function serializeP6ChangeJournal(records: readonly P6ChangeJournalRecord[]): string {
  const bounded = records
    .filter(validRecord)
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

export function parseP6ChangeJournal(raw: string | null): readonly P6ChangeJournalRecord[] {
  if (!raw || new TextEncoder().encode(raw).byteLength > P6_CHANGE_JOURNAL_MAX_BYTES) return []
  try {
    const value: unknown = JSON.parse(raw)
    if (!value || typeof value !== 'object' || Array.isArray(value)) return []
    const journal = value as Record<string, unknown>
    if (
      journal.schemaVersion !== P6_CHANGE_JOURNAL_SCHEMA_VERSION ||
      !Array.isArray(journal.records) ||
      journal.records.length > P6_CHANGE_JOURNAL_MAX_RECORDS ||
      !journal.records.every(validRecord)
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
