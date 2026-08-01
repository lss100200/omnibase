'use client'

import { useState } from 'react'
import { Loader2, Search, Sparkles } from 'lucide-react'
import { toast } from 'sonner'
import { ragApi, getApiErrorMessage } from '@/lib/api'
import type { ChunkResult, PlaygroundResponse } from '@/lib/types'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Separator } from '@/components/ui/separator'

export default function PlaygroundPage() {
  const [query, setQuery] = useState('')
  const [loading, setLoading] = useState(false)
  const [result, setResult] = useState<PlaygroundResponse | null>(null)

  const handleSearch = async () => {
    if (!query.trim()) return
    setLoading(true)
    try {
      const data = await ragApi.playground(query, 5, 100, true)
      setResult(data)
    } catch (err) {
      toast.error('检索失败', { description: getApiErrorMessage(err) })
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="flex items-center gap-2 text-2xl font-bold tracking-tight">
          <Sparkles className="h-6 w-6 text-primary" />
          检索 Playground
        </h1>
        <p className="mt-1 text-sm text-muted-foreground">
          测试 AI RAG 多级检索：向量 + BM25 + RRF 融合 + 重排
        </p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-sm">查询</CardTitle>
        </CardHeader>
        <CardContent className="flex gap-2">
          <Input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="输入问题，如：OmniBase 用了什么嵌入模型？"
            onKeyDown={(e) => e.key === 'Enter' && handleSearch()}
            autoFocus
          />
          <Button onClick={handleSearch} disabled={loading}>
            {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <Search className="h-4 w-4" />}
            检索
          </Button>
        </CardContent>
      </Card>

      {result && (
        <>
          {/* Debug info */}
          <Card>
            <CardHeader className="pb-3">
              <CardTitle className="text-sm">检索详情</CardTitle>
            </CardHeader>
            <CardContent className="flex flex-wrap gap-2 text-xs">
              <Badge variant={result.debug.query_embedded ? 'success' : 'destructive'}>
                Embedding: {result.debug.query_embedded ? 'OK' : 'N/A'}
              </Badge>
              <Badge variant="secondary">向量: {result.debug.vector_results_count}</Badge>
              <Badge variant="secondary">BM25: {result.debug.bm25_results_count}</Badge>
              <Badge variant="secondary">融合: {result.debug.fused_count}</Badge>
              <Badge variant="secondary">重排: {result.debug.reranked_count}</Badge>
              <Badge variant={result.debug.reranker_available ? 'success' : 'warning'}>
                Reranker: {result.debug.reranker_available ? 'OK' : '降级'}
              </Badge>
              <Badge variant="outline">{result.latency_ms}ms</Badge>
            </CardContent>
          </Card>

          {/* Results */}
          <Card>
            <CardHeader className="pb-3">
              <CardTitle className="text-sm">检索结果 ({result.results.length})</CardTitle>
              <CardDescription>按重排分数排序</CardDescription>
            </CardHeader>
            <CardContent className="space-y-3">
              {result.results.map((chunk, i) => (
                <ChunkCard key={chunk.chunk_id} chunk={chunk} rank={i + 1} />
              ))}
            </CardContent>
          </Card>
        </>
      )}
    </div>
  )
}

function ChunkCard({ chunk, rank }: { chunk: ChunkResult; rank: number }) {
  const scorePercent = Math.round(chunk.score * 100)
  return (
    <div className="rounded-lg border p-4">
      <div className="mb-2 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span className="flex h-6 w-6 items-center justify-center rounded-full bg-primary text-xs font-bold text-primary-foreground">
            {rank}
          </span>
          <Badge variant="outline" className="text-xs">
            {chunk.chunk_type}
          </Badge>
          <span className="text-xs text-muted-foreground">P{chunk.page_number}</span>
        </div>
        <div className="flex items-center gap-2">
          <div className="h-2 w-20 overflow-hidden rounded-full bg-muted">
            <div
              className="h-full bg-primary transition-all"
              style={{ width: `${Math.min(scorePercent, 100)}%` }}
            />
          </div>
          <span className="text-xs font-mono text-muted-foreground">{chunk.score.toFixed(4)}</span>
        </div>
      </div>
      <Separator className="mb-2" />
      <p className="text-sm leading-relaxed text-foreground/90">{chunk.content}</p>
    </div>
  )
}
