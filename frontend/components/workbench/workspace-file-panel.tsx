'use client'

import { forwardRef, useEffect, useImperativeHandle, useMemo, useRef, useState } from 'react'
import {
  ChevronDown,
  ChevronRight,
  File,
  FileCode2,
  FileImage,
  FileText,
  Folder,
  FolderOpen,
  Pin,
  RotateCcw,
  Save,
  ShieldAlert,
  Unplug,
} from 'lucide-react'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import {
  admitP6FileTreeEntry,
  bindP6FileViewDigest,
  compileP6FileContext,
  createP6FileViewState,
  emptyP6FileTreeUsage,
  setP6FileMode,
  type P6FileContextCompilation,
  type P6FileMetadata,
  type P6FileTreeUsage,
  type P6FileViewState,
} from '@/lib/p6-files'
import {
  createTaskOwnedChangeSet,
  preflightTaskChangeSetRollback,
  type ChangeSetOwner,
  type TaskOwnedChangeSet,
} from '@/lib/p6-changesets'
import {
  createP6RootEntry,
  listP6Children,
  P6_INTERNAL_PREVIEW_MAX_BYTES,
  p6DirectoryPickerAvailable,
  pickP6Directory,
  readP6Snapshot,
  requireP6Permission,
  writeP6Text,
  type P6HandleEntry,
  type P6ReadSnapshot,
} from '@/lib/p6-file-handles'

export interface P6TaskBinding {
  readonly taskId: string
  readonly invocationId: string
}

export interface WorkspaceFilePanelHandle {
  compileContext(
    baseRequest: string,
    maximumContextCharacters: number,
  ): Promise<P6FileContextCompilation>
}

interface LocalChangeRecord {
  readonly changeSet: TaskOwnedChangeSet
  readonly status: 'applied' | 'rolled_back' | 'conflict' | 'recovery_required'
  readonly note: string
}

interface Props {
  readonly tenantId: string
  readonly workspaceId: string
  readonly sessionId: string
  readonly taskBinding: P6TaskBinding | null
  readonly locked: boolean
  readonly onMutationChange: (mutating: boolean) => void
}

function errorMessage(error: unknown): string {
  const code = error instanceof Error ? error.message : 'p6_file_unknown'
  const messages: Record<string, string> = {
    p6_directory_picker_unavailable: '当前浏览器不支持授权目录。',
    p6_file_permission_required: '目录权限需要由你重新确认。',
    p6_file_permission_denied: '目录权限已被拒绝。',
    p6_file_changed_during_read: '文件在读取过程中发生变化，已拒绝继续。',
    p6_text_file_too_large: '文本超过 1 MiB 的本地编辑上限。',
    p6_changeset_cas_drift: '文件已在预览后变化；为保护外部编辑，本次写入已拒绝。',
    p6_write_verification_failed: '写入后的文件摘要不匹配，必须人工检查或恢复。',
  }
  return messages[code] ?? `文件操作失败：${code}`
}

function entryIcon(metadata: P6FileMetadata) {
  if (metadata.kind === 'directory') return Folder
  if (metadata.fileType?.previewKind === 'image') return FileImage
  if (metadata.fileType?.previewKind === 'text') return FileCode2
  if (metadata.fileType?.previewKind === 'pdf') return FileText
  return File
}

export const WorkspaceFilePanel = forwardRef<WorkspaceFilePanelHandle, Props>(
  function WorkspaceFilePanel(
    { tenantId, workspaceId, sessionId, taskBinding, locked, onMutationChange },
    ref,
  ) {
    const supported = p6DirectoryPickerAvailable()
    const handlesRef = useRef(new Map<string, P6HandleEntry>())
    const [rootId, setRootId] = useState<string | null>(null)
    const [entries, setEntries] = useState<P6FileMetadata[]>([])
    const [children, setChildren] = useState<Record<string, string[]>>({})
    const [expanded, setExpanded] = useState<Set<string>>(new Set())
    const [usage, setUsage] = useState<P6FileTreeUsage>(() => emptyP6FileTreeUsage())
    const [views, setViews] = useState<Record<string, P6FileViewState>>({})
    const [preview, setPreview] = useState<P6ReadSnapshot | null>(null)
    const [draft, setDraft] = useState('')
    const [previewUrl, setPreviewUrl] = useState<string | null>(null)
    const [changes, setChanges] = useState<LocalChangeRecord[]>([])
    const sessionViewsRef = useRef(new Map<string, Record<string, P6FileViewState>>())
    const sessionChangesRef = useRef(new Map<string, LocalChangeRecord[]>())
    const viewsRef = useRef(views)
    const changesRef = useRef(changes)
    const usageRef = useRef(usage)
    const loadingDirectoriesRef = useRef(new Set<string>())
    const mutationInFlightRef = useRef(false)
    const scopeGenerationRef = useRef(0)
    const liveScopeRef = useRef('')
    liveScopeRef.current = `${tenantId}:${workspaceId}:${sessionId}:${scopeGenerationRef.current}`
    viewsRef.current = views
    changesRef.current = changes

    const byId = useMemo(() => new Map(entries.map((entry) => [entry.entryId, entry])), [entries])

    useEffect(() => {
      scopeGenerationRef.current += 1
      loadingDirectoriesRef.current.clear()
      handlesRef.current.clear()
      setRootId(null)
      setEntries([])
      setChildren({})
      setExpanded(new Set())
      setUsage(emptyP6FileTreeUsage())
      usageRef.current = emptyP6FileTreeUsage()
      setViews({})
      setPreview(null)
      setDraft('')
      setChanges([])
      sessionViewsRef.current.clear()
      sessionChangesRef.current.clear()
    }, [tenantId, workspaceId])

    useEffect(() => {
      const storedViews = sessionViewsRef.current
      const storedChanges = sessionChangesRef.current
      setViews(sessionViewsRef.current.get(sessionId) ?? {})
      setPreview(null)
      setDraft('')
      setChanges(sessionChangesRef.current.get(sessionId) ?? [])
      return () => {
        storedViews.set(sessionId, viewsRef.current)
        storedChanges.set(sessionId, changesRef.current)
      }
    }, [sessionId])

    useEffect(() => {
      if (previewUrl) URL.revokeObjectURL(previewUrl)
      if (!preview || preview.metadata.fileType?.previewKind === 'text') {
        setPreviewUrl(null)
        return
      }
      if (
        !['image', 'pdf'].includes(preview.metadata.fileType?.previewKind ?? '') ||
        preview.metadata.sizeBytes > P6_INTERNAL_PREVIEW_MAX_BYTES
      ) {
        setPreviewUrl(null)
        return
      }
      const next = URL.createObjectURL(preview.file)
      setPreviewUrl(next)
      return () => URL.revokeObjectURL(next)
      // The previous URL must be revoked whenever the selected snapshot changes.
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [preview])

    async function authorize(): Promise<void> {
      if (locked) return
      if (!workspaceId) {
        toast.error('请先选择 Workspace')
        return
      }
      const operationScope = liveScopeRef.current
      try {
        const handle = await pickP6Directory()
        if (liveScopeRef.current !== operationScope) return
        await requireP6Permission(handle, 'read', true)
        if (liveScopeRef.current !== operationScope) return
        const root = await createP6RootEntry(handle)
        if (liveScopeRef.current !== operationScope) return
        const firstAdmission = admitP6FileTreeEntry(emptyP6FileTreeUsage(), root.metadata)
        if (!firstAdmission.ok) throw new Error(firstAdmission.code)
        if (liveScopeRef.current !== operationScope) return
        const nextGeneration = scopeGenerationRef.current + 1
        scopeGenerationRef.current = nextGeneration
        liveScopeRef.current = `${tenantId}:${workspaceId}:${sessionId}:${nextGeneration}`
        handlesRef.current = new Map([[root.metadata.entryId, root]])
        setRootId(root.metadata.entryId)
        setEntries([root.metadata])
        setChildren({})
        setExpanded(new Set())
        setUsage(firstAdmission.usage)
        usageRef.current = firstAdmission.usage
        setViews({})
        setPreview(null)
        setChanges([])
      } catch (error) {
        if (liveScopeRef.current !== operationScope) return
        toast.error('目录未授权', { description: errorMessage(error) })
      }
    }

    async function toggleDirectory(entryId: string): Promise<void> {
      if (locked) return
      const operationScope = liveScopeRef.current
      if (expanded.has(entryId)) {
        setExpanded((current) => {
          const next = new Set(current)
          next.delete(entryId)
          return next
        })
        return
      }
      if (!(entryId in children)) {
        const loadingKey = `${operationScope}:${entryId}`
        if (loadingDirectoriesRef.current.has(loadingKey)) return
        loadingDirectoriesRef.current.add(loadingKey)
        const parent = handlesRef.current.get(entryId)
        if (!parent) {
          loadingDirectoriesRef.current.delete(loadingKey)
          return
        }
        try {
          let admissionCode: string | null = null
          let admittedUsage = usageRef.current
          const accepted = await listP6Children(parent, (metadata) => {
            if (liveScopeRef.current !== operationScope) return false
            const admission = admitP6FileTreeEntry(admittedUsage, metadata)
            if (!admission.ok) {
              admissionCode = admission.code
              return false
            }
            admittedUsage = admission.usage
            return true
          })
          if (liveScopeRef.current !== operationScope) return
          if (admissionCode) {
            toast.warning('文件树预算已停止枚举', { description: admissionCode })
          }
          const nextHandles = new Map(handlesRef.current)
          accepted.forEach((item) => nextHandles.set(item.metadata.entryId, item))
          if (liveScopeRef.current !== operationScope) return
          usageRef.current = admittedUsage
          handlesRef.current = nextHandles
          setEntries((current) => [...current, ...accepted.map((item) => item.metadata)])
          setChildren((current) => ({
            ...current,
            [entryId]: accepted.map((item) => item.metadata.entryId),
          }))
          setUsage(usageRef.current)
        } catch (error) {
          if (liveScopeRef.current !== operationScope) return
          toast.error('无法展开目录', { description: errorMessage(error) })
          return
        } finally {
          loadingDirectoriesRef.current.delete(loadingKey)
        }
      }
      if (liveScopeRef.current !== operationScope) return
      setExpanded((current) => new Set(current).add(entryId))
    }

    async function openFile(entryId: string): Promise<void> {
      if (locked) return
      const operationScope = liveScopeRef.current
      const entry = handlesRef.current.get(entryId)
      if (!entry || entry.metadata.kind !== 'file') return
      try {
        const snapshot = await readP6Snapshot(entry)
        if (liveScopeRef.current !== operationScope) return
        setPreview(snapshot)
        setDraft(snapshot.text ?? '')
        setEntries((current) =>
          current.map((item) => (item.entryId === entryId ? snapshot.metadata : item)),
        )
        setViews((current) => ({
          ...current,
          [entryId]: bindP6FileViewDigest(
            setP6FileMode(
              current[entryId] ?? createP6FileViewState(snapshot.metadata),
              'OPEN',
              true,
            ),
            snapshot.version?.kind === 'text' ? snapshot.version.digest : '0'.repeat(64),
          ),
        }))
      } catch (error) {
        if (liveScopeRef.current !== operationScope) return
        toast.error('无法打开文件', { description: errorMessage(error) })
      }
    }

    function toggleMode(entryId: string, mode: 'CONTEXT' | 'PINNED'): void {
      if (locked) return
      const metadata = byId.get(entryId)
      if (!metadata || metadata.fileType?.previewKind !== 'text') {
        toast.error('只有可验证的文本文件可以加入模型上下文')
        return
      }
      setViews((current) => {
        const previous = current[entryId] ?? createP6FileViewState(metadata)
        return {
          ...current,
          [entryId]: setP6FileMode(
            previous,
            mode,
            !previous[mode.toLowerCase() as 'context' | 'pinned'],
          ),
        }
      })
    }

    useImperativeHandle(
      ref,
      () => ({
        async compileContext(baseRequest, maximumContextCharacters) {
          const selected = Object.values(views).filter((state) => state.context || state.pinned)
          const files = []
          for (const state of selected) {
            const entry = handlesRef.current.get(state.entryId)
            if (!entry) return { ok: false, code: 'missing_file', entryId: state.entryId }
            try {
              const snapshot = await readP6Snapshot(entry)
              if (snapshot.text === null) {
                return { ok: false, code: 'file_not_text', entryId: state.entryId }
              }
              if (
                snapshot.version?.kind !== 'text' ||
                !state.expectedDigest ||
                snapshot.version.digest !== state.expectedDigest
              ) {
                return { ok: false, code: 'file_changed', entryId: state.entryId }
              }
              files.push({
                metadata: snapshot.metadata,
                content: snapshot.text,
                digest: snapshot.version.digest,
              })
            } catch {
              return { ok: false, code: 'file_changed', entryId: state.entryId }
            }
          }
          return compileP6FileContext({
            baseRequest,
            states: selected,
            files,
            budget: {
              maxFiles: 16,
              maxFileCharacters: 12_000,
              maxContextCharacters: maximumContextCharacters,
              maxContextTokens: Math.ceil(maximumContextCharacters / 4),
              maxRequestCharacters: 32_000,
            },
          })
        },
      }),
      [views],
    )

    async function saveReviewedEdit(): Promise<void> {
      if (mutationInFlightRef.current) return
      if (!preview || preview.text === null || !preview.version || preview.version.kind !== 'text')
        return
      if (!taskBinding) {
        toast.error('尚无可绑定的成功任务', {
          description: '先完成一次 Agent 调用，再将你审阅后的文本写入并记录 ChangeSet。',
        })
        return
      }
      const entry = handlesRef.current.get(preview.metadata.entryId)
      if (!entry) return
      mutationInFlightRef.current = true
      onMutationChange(true)
      const operationScope = liveScopeRef.current
      try {
        const before = await readP6Snapshot(entry)
        if (!before.version || before.version.kind !== 'text')
          throw new Error('p6_changeset_binary_text')
        if (
          preview.version.kind !== 'text' ||
          before.version.digest !== preview.version.digest ||
          before.metadata.sizeBytes !== preview.metadata.sizeBytes ||
          before.metadata.lastModified !== preview.metadata.lastModified
        ) {
          throw new Error('p6_changeset_cas_drift')
        }
        if (before.text === draft) {
          toast.info('文件内容没有变化')
          return
        }
        const owner: ChangeSetOwner = {
          tenantId,
          workspaceId,
          taskId: taskBinding.taskId,
          attemptId: taskBinding.invocationId,
        }
        const changeSet = await createTaskOwnedChangeSet({
          ...owner,
          id: crypto.randomUUID(),
          createdAt: new Date().toISOString(),
          files: [
            {
              path: before.metadata.logicalPath,
              before: before.version,
              after: { kind: 'text', content: draft },
            },
          ],
        })
        if (liveScopeRef.current !== operationScope) throw new Error('p6_file_scope_changed')
        setChanges((current) => [
          {
            changeSet,
            status: 'recovery_required',
            note: '写入已开始；完成摘要复核前保留任务前内容用于恢复。',
          },
          ...current,
        ])
        const written = await writeP6Text(entry, draft, true)
        if (!written.version || written.version.kind !== 'text')
          throw new Error('p6_write_verification_failed')
        const expected = changeSet.files[0]?.after
        if (expected?.kind !== 'text' || written.version.digest !== expected.digest) {
          throw new Error('p6_write_verification_failed')
        }
        if (liveScopeRef.current !== operationScope) {
          if (written.version.digest === expected.digest) {
            const compensationCas = await readP6Snapshot(entry)
            if (
              compensationCas.version?.kind === 'text' &&
              compensationCas.version.digest === expected.digest
            ) {
              await writeP6Text(entry, before.version.content)
            }
          }
          throw new Error('p6_file_scope_changed')
        }
        setPreview(written)
        setChanges((current) =>
          current.map((item) =>
            item.changeSet.id === changeSet.id
              ? {
                  ...item,
                  status: 'applied',
                  note: '用户审阅后本地写入；非 Agent 自动写盘。',
                }
              : item,
          ),
        )
        toast.success('本地编辑已写入并记录 ChangeSet')
      } catch (error) {
        toast.error('写入失败', { description: errorMessage(error) })
      } finally {
        mutationInFlightRef.current = false
        onMutationChange(false)
      }
    }

    async function rollback(record: LocalChangeRecord): Promise<void> {
      if (mutationInFlightRef.current) return
      const entry = [...handlesRef.current.values()].find(
        (candidate) => candidate.metadata.logicalPath === record.changeSet.files[0]?.path,
      )
      if (!entry) {
        toast.error('回滚目标不在当前授权树中')
        return
      }
      mutationInFlightRef.current = true
      onMutationChange(true)
      const operationScope = liveScopeRef.current
      try {
        const current = await readP6Snapshot(entry)
        if (!current.version) throw new Error('p6_changeset_binary_text')
        const owner: ChangeSetOwner = {
          tenantId,
          workspaceId,
          taskId: record.changeSet.taskId,
          attemptId: record.changeSet.attemptId,
        }
        const preflight = await preflightTaskChangeSetRollback({
          changeSet: record.changeSet,
          owner,
          currentFiles: [{ path: current.metadata.logicalPath, version: current.version }],
          createdAt: new Date().toISOString(),
        })
        if (!preflight.ok) {
          setChanges((items) =>
            items.map((item) =>
              item.changeSet.id === record.changeSet.id
                ? {
                    ...item,
                    status: 'conflict',
                    note: preflight.conflicts.map((conflict) => conflict.code).join(', '),
                  }
                : item,
            ),
          )
          toast.error('回滚冲突，未写入任何内容')
          return
        }
        if (liveScopeRef.current !== operationScope) throw new Error('p6_file_scope_changed')
        const operation = preflight.plan[0]
        if (!operation || operation.action === 'noop') {
          setChanges((items) =>
            items.map((item) =>
              item.changeSet.id === record.changeSet.id
                ? { ...item, status: 'rolled_back', note: '目标已经处于回滚状态。' }
                : item,
            ),
          )
          return
        }
        if (operation.action !== 'write' || operation.resultContent === null) {
          throw new Error('p6_changeset_unsupported_operation')
        }
        const cas = await readP6Snapshot(entry)
        if (!cas.version || cas.version.kind !== 'text') throw new Error('p6_changeset_binary_text')
        if (cas.version.digest !== operation.expectedCurrentDigest)
          throw new Error('p6_changeset_cas_drift')
        if (liveScopeRef.current !== operationScope) throw new Error('p6_file_scope_changed')
        const restored = await writeP6Text(entry, operation.resultContent, true)
        if (
          !restored.version ||
          restored.version.kind !== 'text' ||
          restored.version.digest !== operation.resultDigest
        ) {
          throw new Error('p6_write_verification_failed')
        }
        if (liveScopeRef.current !== operationScope) {
          if (restored.version.digest === operation.resultDigest) {
            const compensationCas = await readP6Snapshot(entry)
            if (
              compensationCas.version?.kind === 'text' &&
              compensationCas.version.digest === operation.resultDigest
            ) {
              await writeP6Text(entry, cas.version.content)
            }
          }
          throw new Error('p6_file_scope_changed')
        }
        setPreview(restored)
        setDraft(restored.text ?? '')
        setChanges((items) =>
          items.map((item) =>
            item.changeSet.id === record.changeSet.id
              ? { ...item, status: 'rolled_back', note: '三方回滚完成并复核摘要。' }
              : item,
          ),
        )
        toast.success('ChangeSet 已安全回滚')
      } catch (error) {
        setChanges((items) =>
          items.map((item) =>
            item.changeSet.id === record.changeSet.id
              ? { ...item, status: 'recovery_required', note: errorMessage(error) }
              : item,
          ),
        )
        toast.error('回滚未完成', { description: errorMessage(error) })
      } finally {
        mutationInFlightRef.current = false
        onMutationChange(false)
      }
    }

    function renderTree(entryId: string, depth = 0): React.ReactNode {
      const metadata = byId.get(entryId)
      if (!metadata) return null
      const Icon = entryIcon(metadata)
      const isDirectory = metadata.kind === 'directory'
      const isExpanded = expanded.has(entryId)
      const state = views[entryId]
      return (
        <div key={entryId}>
          <div
            className="group flex items-center gap-1 rounded px-1 py-1 hover:bg-muted"
            style={{ paddingLeft: `${depth * 10 + 4}px` }}
          >
            <button
              type="button"
              className="flex min-w-0 flex-1 items-center gap-1.5 text-left text-[9px]"
              onClick={() => (isDirectory ? void toggleDirectory(entryId) : void openFile(entryId))}
              disabled={locked}
            >
              {isDirectory ? (
                isExpanded ? (
                  <ChevronDown className="h-3 w-3" />
                ) : (
                  <ChevronRight className="h-3 w-3" />
                )
              ) : (
                <span className="w-3" />
              )}
              <Icon className="h-3.5 w-3.5 shrink-0" />
              <span className="truncate">{metadata.name}</span>
            </button>
            {!isDirectory && (
              <>
                <button
                  type="button"
                  aria-label="加入 Context"
                  onClick={() => toggleMode(entryId, 'CONTEXT')}
                  disabled={locked}
                  className={cn(
                    'rounded px-1 text-[7px]',
                    state?.context ? 'bg-foreground text-background' : 'text-muted-foreground',
                  )}
                >
                  C
                </button>
                <button
                  type="button"
                  aria-label="固定 Context"
                  onClick={() => toggleMode(entryId, 'PINNED')}
                  disabled={locked}
                  className={cn(
                    'rounded p-0.5',
                    state?.pinned ? 'text-foreground' : 'text-muted-foreground',
                  )}
                >
                  <Pin className="h-2.5 w-2.5" />
                </button>
              </>
            )}
          </div>
          {isDirectory &&
            isExpanded &&
            (children[entryId] ?? []).map((child) => renderTree(child, depth + 1))}
        </div>
      )
    }

    return (
      <div className="space-y-3">
        <div className="rounded-xl border bg-background p-2.5">
          <div className="flex items-center gap-2">
            <FolderOpen className="h-3.5 w-3.5" />
            <span className="text-[9px] font-semibold">授权文件树</span>
            <span className="ml-auto font-mono text-[6px] text-muted-foreground">
              {usage.nodes} nodes
            </span>
          </div>
          {!supported ? (
            <p className="mt-2 text-[8px] leading-4 text-muted-foreground">
              此浏览器没有 File System Access API。不会扫描全盘，也不会用下载冒充系统打开。
            </p>
          ) : rootId ? (
            <div className="mt-2 max-h-44 overflow-auto">{renderTree(rootId)}</div>
          ) : (
            <Button
              size="sm"
              variant="outline"
              className="mt-2 h-8 w-full text-[9px]"
              onClick={() => void authorize()}
              disabled={locked || !workspaceId}
            >
              <Folder className="h-3 w-3" />
              选择并授权目录
            </Button>
          )}
          {rootId && (
            <Button
              size="sm"
              variant="ghost"
              className="mt-1 h-7 w-full text-[8px]"
              onClick={() => {
                if (locked) return
                handlesRef.current.clear()
                scopeGenerationRef.current += 1
                liveScopeRef.current = `${tenantId}:${workspaceId}:${sessionId}:${scopeGenerationRef.current}`
                setRootId(null)
                setEntries([])
                setChildren({})
                setViews({})
                setPreview(null)
                const emptyUsage = emptyP6FileTreeUsage()
                usageRef.current = emptyUsage
                setUsage(emptyUsage)
              }}
              disabled={locked}
            >
              <Unplug className="h-3 w-3" />
              解除本页授权
            </Button>
          )}
        </div>

        {preview && (
          <div className="rounded-xl border bg-background p-2.5">
            <div className="flex items-center gap-2">
              <FileCode2 className="h-3.5 w-3.5" />
              <span className="min-w-0 flex-1 truncate text-[9px] font-semibold">
                {preview.metadata.logicalPath}
              </span>
            </div>
            <p className="mt-1 font-mono text-[6px] text-muted-foreground">
              {preview.metadata.fileType?.previewKind} ·{' '}
              {preview.metadata.sizeBytes.toLocaleString()} bytes · OPEN{' '}
              {views[preview.metadata.entryId]?.context ? '· CONTEXT' : ''}{' '}
              {views[preview.metadata.entryId]?.pinned ? '· PINNED' : ''}
            </p>
            {preview.text !== null ? (
              <>
                <textarea
                  value={draft}
                  onChange={(event) => setDraft(event.target.value)}
                  className="mt-2 h-40 w-full resize-y rounded-lg border bg-muted/20 p-2 font-mono text-[8px] outline-none"
                  spellCheck={false}
                  disabled={locked}
                />
                <div className="mt-2 flex gap-1">
                  <Button
                    size="sm"
                    className="h-7 text-[8px]"
                    onClick={() => void saveReviewedEdit()}
                    disabled={locked || draft === preview.text}
                  >
                    <Save className="h-3 w-3" />
                    审阅后写入
                  </Button>
                  <Button
                    size="sm"
                    variant="outline"
                    className="h-7 text-[8px]"
                    disabled
                    title="浏览器无法可靠调用系统默认应用"
                  >
                    系统打开需原生桥接
                  </Button>
                </div>
              </>
            ) : previewUrl && preview.metadata.fileType?.previewKind === 'image' ? (
              // eslint-disable-next-line @next/next/no-img-element
              <img
                src={previewUrl}
                alt={preview.metadata.name}
                className="mt-2 max-h-48 w-full rounded-lg border object-contain"
              />
            ) : previewUrl && preview.metadata.fileType?.previewKind === 'pdf' ? (
              <iframe
                src={previewUrl}
                title={preview.metadata.name}
                className="mt-2 h-56 w-full rounded-lg border"
              />
            ) : (
              <p className="mt-2 rounded-lg border border-dashed p-3 text-[8px] text-muted-foreground">
                {preview.metadata.sizeBytes > P6_INTERNAL_PREVIEW_MAX_BYTES
                  ? '文件超过 20 MiB 内部预览上限，仅显示元数据。'
                  : '该二进制类型仅显示元数据，不注入模型上下文。'}
              </p>
            )}
          </div>
        )}

        <div className="rounded-xl border bg-background p-2.5">
          <div className="flex items-center gap-2">
            <RotateCcw className="h-3.5 w-3.5" />
            <span className="text-[9px] font-semibold">任务修改记录</span>
          </div>
          <p className="mt-1 text-[7px] leading-3 text-muted-foreground">
            当前 Runtime 没有文件工具；这里只记录你依据成功任务审阅后写入的本地文本，不声称 Agent
            自动改盘。
          </p>
          {changes.length === 0 ? (
            <p className="mt-2 text-[8px] text-muted-foreground">暂无 ChangeSet</p>
          ) : (
            changes.map((record) => (
              <div key={record.changeSet.id} className="mt-2 rounded-lg border p-2 text-[7px]">
                <div className="flex items-center gap-1">
                  <span className="min-w-0 flex-1 truncate font-mono">
                    {record.changeSet.files[0]?.path}
                  </span>
                  <span className="uppercase text-muted-foreground">{record.status}</span>
                </div>
                <p className="mt-1 text-muted-foreground">
                  task {record.changeSet.taskId.slice(0, 8)} · {record.note}
                </p>
                <details className="mt-2 rounded border bg-muted/20 p-1.5">
                  <summary className="cursor-pointer text-[7px]">查看 Before / After 审计</summary>
                  <div className="mt-1 grid gap-1">
                    <pre className="max-h-24 overflow-auto whitespace-pre-wrap rounded bg-background p-1 text-[6px]">
                      BEFORE\n
                      {record.changeSet.files[0]?.before.kind === 'text'
                        ? record.changeSet.files[0].before.content
                        : `[${record.changeSet.files[0]?.before.kind}]`}
                    </pre>
                    <pre className="max-h-24 overflow-auto whitespace-pre-wrap rounded bg-background p-1 text-[6px]">
                      AFTER\n
                      {record.changeSet.files[0]?.after.kind === 'text'
                        ? record.changeSet.files[0].after.content
                        : `[${record.changeSet.files[0]?.after.kind}]`}
                    </pre>
                  </div>
                </details>
                {(record.status === 'applied' || record.status === 'recovery_required') && (
                  <Button
                    size="sm"
                    variant="outline"
                    className="mt-2 h-7 text-[8px]"
                    onClick={() => void rollback(record)}
                    disabled={locked}
                  >
                    <RotateCcw className="h-3 w-3" />
                    {record.status === 'recovery_required' ? '尝试恢复任务前内容' : '一键三方回滚'}
                  </Button>
                )}
              </div>
            ))
          )}
          <div className="mt-2 flex gap-2 rounded-lg border border-dashed p-2 text-[7px] leading-3 text-muted-foreground">
            <ShieldAlert className="h-3.5 w-3.5 shrink-0" />
            <span>
              写前再次校验摘要、写后复核结果；浏览器不具备跨文件原子事务，异常时会标记 recovery
              required。
            </span>
          </div>
        </div>
      </div>
    )
  },
)
