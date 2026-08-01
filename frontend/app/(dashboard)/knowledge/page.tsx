'use client'

import { useCallback, useEffect, useRef, useState } from 'react'
import useSWR from 'swr'
import {
  ChevronLeft,
  ChevronRight,
  Download,
  FileText,
  Loader2,
  Trash2,
  Upload,
  UploadCloud,
} from 'lucide-react'
import { toast } from 'sonner'
import { documentsApi } from '@/lib/api'
import { getApiErrorMessage } from '@/lib/api'
import type { DocumentRead, DocumentStatus } from '@/lib/types'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { formatBytes, formatDateTime } from '@/lib/utils'

const POLL_INTERVAL = 5_000 // Poll active documents every 5s
const PAGE_SIZE = 20
const STATUS_BADGE: Record<
  DocumentStatus,
  { variant: 'success' | 'warning' | 'destructive' | 'secondary'; label: string }
> = {
  pending: { variant: 'secondary', label: '待入队' },
  queued: { variant: 'warning', label: '排队中' },
  processing: { variant: 'warning', label: '处理中' },
  indexed: { variant: 'success', label: '已索引' },
  failed: { variant: 'destructive', label: '失败' },
}

export default function KnowledgePage() {
  const fileInputRef = useRef<HTMLInputElement>(null)
  const [uploading, setUploading] = useState(false)
  const [page, setPage] = useState(1)
  const [confirmDelete, setConfirmDelete] = useState<DocumentRead | null>(null)
  const offset = (page - 1) * PAGE_SIZE
  const documentsKey = ['documents', PAGE_SIZE, offset] as const

  const { data, isLoading, isValidating, error, mutate } = useSWR(
    documentsKey,
    ([, limit, currentOffset]) => documentsApi.list({ limit, offset: currentOffset }),
    {
      keepPreviousData: true,
      refreshInterval: (latest) => {
        // Keep polling while any document on the visible page is in an active state.
        const hasActive = latest?.items.some(
          (d) => d.status === 'pending' || d.status === 'queued' || d.status === 'processing',
        )
        return hasActive ? POLL_INTERVAL : 0
      },
    },
  )

  const totalPages = Math.max(1, Math.ceil((data?.total ?? 0) / PAGE_SIZE))

  useEffect(() => {
    if (page > totalPages) setPage(totalPages)
  }, [page, totalPages])

  const handleUpload = useCallback(
    async (files: FileList | null) => {
      if (!files || files.length === 0) return
      setUploading(true)
      let successCount = 0
      let failCount = 0

      // Sequential upload (Phase 0); Phase 1 will parallelize
      for (const file of Array.from(files)) {
        try {
          const result = await documentsApi.upload(file)
          successCount++
          toast.success(`已上传 ${file.name}`, {
            description: result.message,
          })
        } catch (err) {
          failCount++
          toast.error(`上传失败 ${file.name}`, {
            description: getApiErrorMessage(err),
          })
        }
      }

      // New uploads appear first, so return to the first page before refreshing.
      if (successCount > 0) {
        setPage(1)
        if (page === 1) await mutate()
      }
      if (failCount > 0 && successCount > 0) {
        toast.info(`部分成功`, { description: `${successCount} 个成功，${failCount} 个失败` })
      }
      setUploading(false)
      // Reset file input so same file can be selected again
      if (fileInputRef.current) fileInputRef.current.value = ''
    },
    [mutate, page],
  )

  const handleDownload = async (doc: DocumentRead) => {
    try {
      const { url } = await documentsApi.downloadUrl(doc.id)
      window.open(url, '_blank')
    } catch (err) {
      toast.error('获取下载链接失败', { description: getApiErrorMessage(err) })
    }
  }

  const handleDelete = async () => {
    if (!confirmDelete) return
    const doc = confirmDelete
    setConfirmDelete(null)
    try {
      await documentsApi.delete(doc.id)
      toast.success(`已删除 ${doc.filename}`)
      await mutate()
    } catch (err) {
      toast.error('删除失败', { description: getApiErrorMessage(err) })
    }
  }

  const handleDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault()
      handleUpload(e.dataTransfer.files)
    },
    [handleUpload],
  )

  const documents = data?.items ?? []

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">知识库</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            上传文档以构建你的专属知识库，{data ? `共 ${data.total} 个` : '正在加载…'}
          </p>
        </div>
      </div>

      {/* Upload dropzone */}
      <Card className="border-dashed" onDragOver={(e) => e.preventDefault()} onDrop={handleDrop}>
        <CardContent className="flex flex-col items-center justify-center gap-3 py-10">
          <div className="flex h-12 w-12 items-center justify-center rounded-full bg-muted">
            <UploadCloud className="h-6 w-6 text-muted-foreground" />
          </div>
          <div className="text-center">
            <p className="text-sm font-medium">拖拽文件到此处，或</p>
            <Button
              variant="outline"
              size="sm"
              className="mt-2"
              disabled={uploading}
              onClick={() => fileInputRef.current?.click()}
            >
              {uploading ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Upload className="h-4 w-4" />
              )}
              选择文件
            </Button>
          </div>
          <p className="text-xs text-muted-foreground">
            支持 PDF / DOCX / TXT / Markdown，单个文件最大 50 MB
          </p>
          <input
            ref={fileInputRef}
            type="file"
            multiple
            accept=".pdf,.docx,.txt,.md"
            className="hidden"
            onChange={(e) => handleUpload(e.target.files)}
          />
        </CardContent>
      </Card>

      {/* Documents list */}
      <Card>
        <CardContent className="p-0">
          {isLoading ? (
            <div className="flex h-32 items-center justify-center">
              <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
            </div>
          ) : error ? (
            <div className="flex h-32 flex-col items-center justify-center gap-2 text-center">
              <p className="text-sm text-destructive">加载失败</p>
              <p className="text-xs text-muted-foreground">{getApiErrorMessage(error)}</p>
              <Button variant="outline" size="sm" onClick={() => mutate()} disabled={isValidating}>
                {isValidating && <Loader2 className="h-4 w-4 animate-spin" />}
                重试
              </Button>
            </div>
          ) : documents.length === 0 ? (
            <div className="flex h-32 flex-col items-center justify-center gap-2 text-center">
              <FileText className="h-8 w-8 text-muted-foreground" />
              <p className="text-sm text-muted-foreground">还没有任何文档</p>
              <p className="text-xs text-muted-foreground">上传第一份文档开始构建你的知识库</p>
            </div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>文件名</TableHead>
                  <TableHead className="w-24">大小</TableHead>
                  <TableHead className="w-24">状态</TableHead>
                  <TableHead className="w-20">页数</TableHead>
                  <TableHead className="w-40">上传时间</TableHead>
                  <TableHead className="w-24 text-right">操作</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {documents.map((doc) => {
                  const badge = STATUS_BADGE[doc.status]
                  return (
                    <TableRow key={doc.id}>
                      <TableCell className="max-w-xs truncate font-medium" title={doc.filename}>
                        {doc.filename}
                      </TableCell>
                      <TableCell className="text-muted-foreground">
                        {formatBytes(doc.size_bytes)}
                      </TableCell>
                      <TableCell>
                        <Badge
                          variant={badge.variant}
                          title={
                            doc.status === 'failed' && doc.error_detail
                              ? doc.error_detail
                              : undefined
                          }
                        >
                          {badge.label}
                        </Badge>
                      </TableCell>
                      <TableCell className="text-muted-foreground">
                        {doc.page_count ?? '—'}
                      </TableCell>
                      <TableCell className="text-muted-foreground">
                        {formatDateTime(doc.created_at)}
                      </TableCell>
                      <TableCell className="text-right">
                        <div className="flex justify-end gap-1">
                          <Button
                            variant="ghost"
                            size="icon"
                            onClick={() => handleDownload(doc)}
                            title="下载"
                          >
                            <Download className="h-4 w-4" />
                          </Button>
                          <Button
                            variant="ghost"
                            size="icon"
                            onClick={() => setConfirmDelete(doc)}
                            title="删除"
                            className="text-destructive hover:text-destructive"
                          >
                            <Trash2 className="h-4 w-4" />
                          </Button>
                        </div>
                      </TableCell>
                    </TableRow>
                  )
                })}
              </TableBody>
            </Table>
          )}
        </CardContent>
        {!isLoading && !error && data && data.total > 0 && (
          <div className="flex flex-col gap-3 border-t px-4 py-3 text-sm sm:flex-row sm:items-center sm:justify-between">
            <p className="text-muted-foreground">
              第 {page} / {totalPages} 页 · 显示 {offset + 1}–
              {Math.min(offset + documents.length, data.total)} 条，共 {data.total} 条
            </p>
            <div className="flex items-center gap-2">
              {isValidating && !isLoading && (
                <span className="flex items-center gap-1 text-xs text-muted-foreground">
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  更新中
                </span>
              )}
              <Button
                variant="outline"
                size="sm"
                onClick={() => setPage((current) => Math.max(1, current - 1))}
                disabled={page === 1}
              >
                <ChevronLeft className="h-4 w-4" />
                上一页
              </Button>
              <Button
                variant="outline"
                size="sm"
                onClick={() => setPage((current) => Math.min(totalPages, current + 1))}
                disabled={page === totalPages}
              >
                下一页
                <ChevronRight className="h-4 w-4" />
              </Button>
            </div>
          </div>
        )}
      </Card>

      {/* Delete confirmation */}
      <Dialog open={!!confirmDelete} onOpenChange={(open) => !open && setConfirmDelete(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>确认删除</DialogTitle>
            <DialogDescription>
              即将删除 <strong>{confirmDelete?.filename}</strong>。该操作不可撤销，文件将从
              数据库和存储中永久移除。
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setConfirmDelete(null)}>
              取消
            </Button>
            <Button variant="destructive" onClick={handleDelete}>
              删除
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
