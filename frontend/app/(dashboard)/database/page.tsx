'use client'

import useSWR from 'swr'
import { Database, Loader2, RefreshCw } from 'lucide-react'
import { toast } from 'sonner'
import { databaseApi, getApiErrorMessage } from '@/lib/api'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'

/** Metadata-only browser for the current tenant's non-sensitive tables. */
export default function DatabasePage() {
  const {
    data: tablesData,
    isLoading: tablesLoading,
    mutate: refetchTables,
  } = useSWR('database-tables', () => databaseApi.listTables(), {
    onError: (err) => {
      toast.error('加载表列表失败', { description: getApiErrorMessage(err) })
    },
  })

  const tables = tablesData?.tables ?? []

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">数据库</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            浏览当前工作空间的非敏感数据结构。原始 SQL 查询已禁用。
          </p>
        </div>
        <Button variant="outline" size="sm" onClick={() => refetchTables()}>
          <RefreshCw className="h-4 w-4" />
          刷新
        </Button>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Database className="h-5 w-5" />
            可见表（{tables.length}）
          </CardTitle>
          <CardDescription>
            身份验证、对象存储、文档内容、向量索引及内部任务字段不会在此显示。
          </CardDescription>
        </CardHeader>
        <CardContent>
          {tablesLoading ? (
            <div className="flex justify-center p-8">
              <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
            </div>
          ) : tables.length === 0 ? (
            <p className="py-8 text-center text-sm text-muted-foreground">暂无可见表。</p>
          ) : (
            <div className="space-y-6">
              {tables.map((table) => (
                <div key={table.name} className="rounded-md border">
                  <div className="flex items-center justify-between border-b px-4 py-3">
                    <span className="font-mono text-sm font-medium">{table.name}</span>
                    <span className="text-xs text-muted-foreground">
                      约 {table.row_count_estimate} 行
                    </span>
                  </div>
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead>列名</TableHead>
                        <TableHead>类型</TableHead>
                        <TableHead>可为空</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {table.columns.map((column) => (
                        <TableRow key={column.name}>
                          <TableCell className="font-mono text-xs">{column.name}</TableCell>
                          <TableCell className="font-mono text-xs">{column.type}</TableCell>
                          <TableCell>{column.nullable ? '是' : '否'}</TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
