import {
  P6_FILE_TYPE_SAMPLE_BYTES,
  createP6FileMetadata,
  validateP6FileName,
  type P6FileMetadata,
} from '@/lib/p6-files'
import { sha256Text, type FileVersion } from '@/lib/p6-changesets'

export type P6PermissionState = 'granted' | 'denied' | 'prompt'

export interface P6FileHandle {
  readonly kind: 'file'
  readonly name: string
  getFile(): Promise<File>
  createWritable(): Promise<{
    write(data: string): Promise<void>
    close(): Promise<void>
    abort?(): Promise<void>
  }>
  queryPermission?(descriptor: { mode: 'read' | 'readwrite' }): Promise<P6PermissionState>
  requestPermission?(descriptor: { mode: 'read' | 'readwrite' }): Promise<P6PermissionState>
}

export interface P6DirectoryHandle {
  readonly kind: 'directory'
  readonly name: string
  values(): AsyncIterableIterator<P6DirectoryHandle | P6FileHandle>
  queryPermission?(descriptor: { mode: 'read' | 'readwrite' }): Promise<P6PermissionState>
  requestPermission?(descriptor: { mode: 'read' | 'readwrite' }): Promise<P6PermissionState>
}

interface P6PickerWindow extends Window {
  showDirectoryPicker?: (options?: { mode?: 'read' | 'readwrite' }) => Promise<P6DirectoryHandle>
}

export interface P6HandleEntry {
  readonly metadata: P6FileMetadata
  readonly handle: P6DirectoryHandle | P6FileHandle
}

export interface P6ReadSnapshot {
  readonly metadata: P6FileMetadata
  readonly bytes: Uint8Array
  readonly text: string | null
  readonly file: File
  readonly version: FileVersion | null
}

export const P6_TEXT_EDIT_MAX_BYTES = 1024 * 1024
export const P6_INTERNAL_PREVIEW_MAX_BYTES = 20 * 1024 * 1024

export interface P6AsyncScopeFence {
  readonly capturedScope: string
  isCurrent(): boolean
  commit(action: () => void): boolean
}

export function createP6AsyncScopeFence(getCurrentScope: () => string): P6AsyncScopeFence {
  const capturedScope = getCurrentScope()
  return {
    capturedScope,
    isCurrent: () => getCurrentScope() === capturedScope,
    commit(action) {
      if (getCurrentScope() !== capturedScope) return false
      action()
      return true
    },
  }
}

function opaqueId(): string {
  return crypto.randomUUID().replaceAll('-', '_')
}

export function p6DirectoryPickerAvailable(): boolean {
  return (
    typeof window !== 'undefined' &&
    typeof (window as P6PickerWindow).showDirectoryPicker === 'function'
  )
}

export async function pickP6Directory(): Promise<P6DirectoryHandle> {
  const picker = (window as P6PickerWindow).showDirectoryPicker
  if (!picker) throw new Error('p6_directory_picker_unavailable')
  return picker({ mode: 'read' })
}

export async function requireP6Permission(
  handle: P6DirectoryHandle | P6FileHandle,
  mode: 'read' | 'readwrite',
  allowPrompt: boolean,
): Promise<void> {
  const current = (await handle.queryPermission?.({ mode })) ?? 'prompt'
  if (current === 'granted') return
  if (allowPrompt && handle.requestPermission) {
    const requested = await handle.requestPermission({ mode })
    if (requested === 'granted') return
  }
  throw new Error(
    current === 'denied' ? 'p6_file_permission_denied' : 'p6_file_permission_required',
  )
}

export async function createP6RootEntry(handle: P6DirectoryHandle): Promise<P6HandleEntry> {
  await requireP6Permission(handle, 'read', false)
  const result = createP6FileMetadata({
    entryId: opaqueId(),
    name: handle.name,
    kind: 'directory',
  })
  if (!result.ok) throw new Error(`p6_root_${result.code}`)
  return { metadata: result.metadata, handle }
}

export async function listP6Children(
  parent: P6HandleEntry,
  admit: (metadata: P6FileMetadata) => boolean = () => true,
): Promise<P6HandleEntry[]> {
  if (parent.handle.kind !== 'directory') throw new Error('p6_not_directory')
  await requireP6Permission(parent.handle, 'read', false)
  const children: P6HandleEntry[] = []
  for await (const handle of parent.handle.values()) {
    if (validateP6FileName(handle.name) !== 'valid') continue
    let file: File | null = null
    if (handle.kind === 'file') file = await handle.getFile()
    const sample = file
      ? new Uint8Array(await file.slice(0, P6_FILE_TYPE_SAMPLE_BYTES).arrayBuffer())
      : undefined
    const result = createP6FileMetadata({
      entryId: opaqueId(),
      parentId: parent.metadata.entryId,
      parentLogicalPath: parent.metadata.logicalPath,
      name: handle.name,
      kind: handle.kind,
      sizeBytes: file?.size,
      lastModified: file?.lastModified,
      sample,
      declaredMediaType: file?.type,
    })
    if (!result.ok) continue
    if (!admit(result.metadata)) break
    children.push({ metadata: result.metadata, handle })
  }
  return children.sort((left, right) => {
    if (left.metadata.kind !== right.metadata.kind)
      return left.metadata.kind === 'directory' ? -1 : 1
    return left.metadata.name.localeCompare(right.metadata.name)
  })
}

function decodeText(bytes: Uint8Array, encoding: 'utf-8' | 'utf-16le' | 'utf-16be'): string {
  if (encoding === 'utf-16le') return new TextDecoder('utf-16le', { fatal: true }).decode(bytes)
  if (encoding === 'utf-16be') {
    const swapped = bytes.slice()
    for (let index = 0; index + 1 < swapped.length; index += 2) {
      const first = swapped[index]!
      swapped[index] = swapped[index + 1]!
      swapped[index + 1] = first
    }
    return new TextDecoder('utf-16le', { fatal: true }).decode(swapped)
  }
  return new TextDecoder('utf-8', { fatal: true }).decode(bytes)
}

export async function readP6Snapshot(entry: P6HandleEntry): Promise<P6ReadSnapshot> {
  if (entry.handle.kind !== 'file') throw new Error('p6_not_file')
  await requireP6Permission(entry.handle, 'read', false)
  const before = await entry.handle.getFile()
  const sample = new Uint8Array(await before.slice(0, P6_FILE_TYPE_SAMPLE_BYTES).arrayBuffer())
  const after = await entry.handle.getFile()
  if (before.size !== after.size || before.lastModified !== after.lastModified) {
    throw new Error('p6_file_changed_during_read')
  }
  const result = createP6FileMetadata({
    entryId: entry.metadata.entryId,
    parentId: entry.metadata.parentId,
    parentLogicalPath: entry.metadata.logicalPath.split('/').slice(0, -1).join('/') || null,
    name: entry.metadata.name,
    kind: 'file',
    sizeBytes: before.size,
    lastModified: before.lastModified,
    sample,
    declaredMediaType: before.type,
  })
  if (!result.ok) throw new Error(`p6_file_${result.code}`)
  const fileType = result.metadata.fileType
  if (fileType?.previewKind === 'text' && before.size > P6_TEXT_EDIT_MAX_BYTES) {
    throw new Error('p6_text_file_too_large')
  }
  const bytes =
    fileType?.previewKind === 'text' ? new Uint8Array(await before.arrayBuffer()) : sample
  const text =
    fileType?.previewKind === 'text' ? decodeText(bytes, fileType.textEncoding ?? 'utf-8') : null
  const version: FileVersion | null =
    text === null ? null : { kind: 'text', content: text, digest: await sha256Text(text) }
  const finalState = await entry.handle.getFile()
  if (before.size !== finalState.size || before.lastModified !== finalState.lastModified) {
    throw new Error('p6_file_changed_during_read')
  }
  if (fileType?.previewKind === 'text') {
    const verificationBytes = new Uint8Array(await finalState.arrayBuffer())
    const verificationText = decodeText(verificationBytes, fileType.textEncoding ?? 'utf-8')
    const afterVerification = await entry.handle.getFile()
    if (
      finalState.size !== afterVerification.size ||
      finalState.lastModified !== afterVerification.lastModified ||
      verificationText !== text
    ) {
      throw new Error('p6_file_changed_during_read')
    }
  }
  return { metadata: result.metadata, bytes, text, file: before, version }
}

export async function writeP6Text(
  entry: P6HandleEntry,
  content: string,
  allowPermissionPrompt = false,
): Promise<P6ReadSnapshot> {
  if (entry.handle.kind !== 'file') throw new Error('p6_not_file')
  await requireP6Permission(entry.handle, 'readwrite', allowPermissionPrompt)
  const writer = await entry.handle.createWritable()
  try {
    await writer.write(content)
    await writer.close()
  } catch (error) {
    await writer.abort?.().catch(() => undefined)
    throw error
  }
  return readP6Snapshot(entry)
}
