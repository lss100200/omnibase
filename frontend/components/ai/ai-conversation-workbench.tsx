'use client'

import { memo, useEffect, useRef, useState } from 'react'
import {
  Bot,
  CheckCircle2,
  FileSearch,
  Send,
  ShieldCheck,
  Sparkles,
  Square,
  User,
  Wrench,
} from 'lucide-react'
import { toast } from 'sonner'
import { ragApi, getApiErrorMessage } from '@/lib/api'
import { createTrailingThrottle, isNearBottom } from '@/lib/chat-performance'
import { takeRagStreamEvents } from '@/lib/rag-stream'
import type { Citation } from '@/lib/types'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'

const MAX_STREAM_DURATION_MS = 120_000
const STREAM_RENDER_INTERVAL_MS = 40
const NEAR_BOTTOM_THRESHOLD_PX = 96

const promptSuggestions = [
  '总结我最近上传的知识，并标出引用来源',
  '帮我把这个想法拆成一个可以执行的工作计划',
  '检查现有资料中是否存在相互矛盾的结论',
] as const

interface Message {
  readonly id: string
  readonly role: 'user' | 'assistant'
  readonly content: string
  readonly citations?: readonly Citation[]
}

interface ActiveResponse {
  readonly id: string
  readonly content: string
  readonly citations: readonly Citation[]
}

let fallbackMessageId = 0

function createMessageId(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID()
  }
  fallbackMessageId += 1
  return `chat-message-${Date.now()}-${fallbackMessageId}`
}

export function AIConversationWorkbench({
  className,
  contextLabel = '当前租户知识库',
  embedded = false,
}: {
  className?: string
  contextLabel?: string
  embedded?: boolean
}) {
  const [messages, setMessages] = useState<Message[]>([])
  const [activeResponse, setActiveResponse] = useState<ActiveResponse | null>(null)
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const scrollRef = useRef<HTMLDivElement>(null)
  const shouldAutoScrollRef = useRef(true)
  const abortControllerRef = useRef<AbortController | null>(null)
  const generationRef = useRef(0)
  const mountedRef = useRef(true)
  const cancelPendingRenderRef = useRef<(() => void) | null>(null)

  useEffect(() => {
    const container = scrollRef.current
    if (!container || !shouldAutoScrollRef.current) return

    if (messages.length === 0 && activeResponse === null) {
      container.scrollTop = 0
      return
    }

    container.scrollTo({
      top: container.scrollHeight,
      behavior: activeResponse ? 'auto' : 'smooth',
    })
  }, [activeResponse, messages])

  useEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
      generationRef.current += 1
      cancelPendingRenderRef.current?.()
      abortControllerRef.current?.abort()
    }
  }, [])

  const handleScroll = () => {
    const container = scrollRef.current
    if (container) {
      shouldAutoScrollRef.current = isNearBottom(container, NEAR_BOTTOM_THRESHOLD_PX)
    }
  }

  const handleSend = async () => {
    const userText = input.trim()
    if (!userText || loading) return

    const generation = generationRef.current + 1
    generationRef.current = generation
    const isCurrentGeneration = () => mountedRef.current && generationRef.current === generation

    const assistantId = createMessageId()
    let answerText = ''
    let citations: readonly Citation[] = []
    let receivedDone = false
    let terminalEventReceived = false

    setInput('')
    setMessages((previous) => [
      ...previous,
      { id: createMessageId(), role: 'user', content: userText },
    ])
    setActiveResponse({ id: assistantId, content: '', citations: [] })
    setLoading(true)

    const renderThrottle = createTrailingThrottle<string>(STREAM_RENDER_INTERVAL_MS, (content) => {
      if (!isCurrentGeneration()) return
      setActiveResponse((current) =>
        current?.id === assistantId ? { ...current, content } : current,
      )
    })
    cancelPendingRenderRef.current = renderThrottle.cancel

    const controller = new AbortController()
    abortControllerRef.current = controller
    let didTimeout = false
    const timeoutId = setTimeout(() => {
      didTimeout = true
      controller.abort()
    }, MAX_STREAM_DURATION_MS)

    let reader: ReadableStreamDefaultReader<Uint8Array> | null = null

    try {
      const response = await ragApi.askStream(userText, 5, { signal: controller.signal })
      if (!response.ok) throw new Error(`HTTP ${response.status}`)
      if (!response.body) throw new Error('No response body')

      reader = response.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''

      while (!terminalEventReceived) {
        const { done, value } = await reader.read()
        buffer += done ? decoder.decode() : decoder.decode(value, { stream: true })
        const [events, remaining] = takeRagStreamEvents(buffer, done)
        buffer = remaining

        for (const event of events) {
          if (terminalEventReceived) break

          switch (event.kind) {
            case 'chunk':
              answerText += event.content
              renderThrottle.push(answerText)
              break
            case 'citations':
              citations = [...event.citations]
              if (isCurrentGeneration()) {
                setActiveResponse((current) =>
                  current?.id === assistantId ? { ...current, citations } : current,
                )
              }
              break
            case 'done':
              terminalEventReceived = true
              receivedDone = true
              if (event.answer !== null) answerText = event.answer
              if (event.citations !== null) citations = [...event.citations]
              renderThrottle.flush()
              if (isCurrentGeneration()) {
                setMessages((previous) => [
                  ...previous,
                  { id: assistantId, role: 'assistant', content: answerText, citations },
                ])
                setActiveResponse(null)
              }
              break
            case 'error':
              terminalEventReceived = true
              throw new Error(event.message || '回答生成失败，请重试')
          }
        }

        if (done) break
      }

      if (!receivedDone) throw new Error('回答流意外中断，请重试')
    } catch (error: unknown) {
      renderThrottle.cancel()
      if (!isCurrentGeneration()) return

      const wasAborted = controller.signal.aborted
      const errorText = didTimeout
        ? '回答超时，请重试'
        : wasAborted
          ? '回答已取消'
          : getApiErrorMessage(error, '回答生成失败')

      setMessages((previous) => [
        ...previous,
        { id: assistantId, role: 'assistant', content: `[错误] ${errorText}` },
      ])
      setActiveResponse(null)
      toast.error(didTimeout ? '问答超时' : wasAborted ? '问答已取消' : '问答失败', {
        description: errorText,
      })
    } finally {
      clearTimeout(timeoutId)
      renderThrottle.cancel()
      try {
        await reader?.cancel()
      } catch {
        // The reader may already be closed or errored.
      }
      if (abortControllerRef.current === controller) abortControllerRef.current = null
      if (cancelPendingRenderRef.current === renderThrottle.cancel) {
        cancelPendingRenderRef.current = null
      }
      if (isCurrentGeneration()) setLoading(false)
    }
  }

  const hasConversation = messages.length > 0 || activeResponse !== null

  return (
    <section
      className={cn(
        'ai-workbench flex min-h-0 flex-col overflow-hidden rounded-2xl border border-indigo-400/15 bg-[#080d1e] text-slate-100 shadow-[0_28px_80px_-48px_rgba(49,46,129,.95)]',
        className,
      )}
      aria-label="OmniBase AI 工作区"
    >
      <header className="flex h-14 shrink-0 items-center justify-between gap-3 border-b border-white/[0.07] px-4 sm:px-5">
        <div className="flex min-w-0 items-center gap-3">
          <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-gradient-to-br from-indigo-500/25 to-cyan-400/15 text-indigo-200 ring-1 ring-indigo-300/15">
            <Bot className="h-4 w-4" />
          </span>
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <h1 className="truncate text-sm font-semibold">OmniBase AI</h1>
              <span className="rounded-full bg-emerald-400/10 px-2 py-0.5 font-mono text-[7px] uppercase tracking-wider text-emerald-300">
                Available
              </span>
            </div>
            <p className="truncate text-[9px] text-slate-500">{contextLabel}</p>
          </div>
        </div>
        <div className="flex items-center rounded-lg border border-white/[0.07] bg-white/[0.025] p-0.5 text-[9px]">
          <span className="rounded-md bg-indigo-400/15 px-2.5 py-1 text-indigo-200">问答</span>
          <span className="px-2.5 py-1 text-slate-600">规划 · Preview</span>
          <span className="hidden px-2.5 py-1 text-slate-700 sm:block">执行 · Locked</span>
        </div>
      </header>

      <div
        ref={scrollRef}
        onScroll={handleScroll}
        className="min-h-0 flex-1 overflow-y-auto px-4 py-5 sm:px-6 sm:py-6"
      >
        {!hasConversation ? (
          <div className="mx-auto flex h-full min-h-full max-w-3xl flex-col items-center justify-center text-center">
            <span className="flex h-14 w-14 items-center justify-center rounded-2xl bg-indigo-400/10 text-indigo-200 ring-1 ring-indigo-300/15">
              <Sparkles className="h-6 w-6" />
            </span>
            <p className="mt-5 font-mono text-[8px] uppercase tracking-[0.22em] text-indigo-300/60">
              AI-first workspace
            </p>
            <h2 className="mt-2 text-3xl font-semibold tracking-[-0.055em] text-white sm:text-5xl">
              从一个问题开始
            </h2>
            <p className="mt-3 max-w-xl text-xs leading-5 text-slate-400 sm:text-sm">
              先和人工智能一起理解问题，再组织知识、工作空间和可验证的引用。
            </p>
            <div className="mt-7 grid w-full gap-2 sm:grid-cols-3">
              {promptSuggestions.map((suggestion) => (
                <button
                  key={suggestion}
                  type="button"
                  onClick={() => setInput(suggestion)}
                  className="rounded-xl border border-white/[0.07] bg-white/[0.025] px-3 py-3 text-left text-[10px] leading-4 text-slate-400 transition-colors hover:border-indigo-300/25 hover:bg-indigo-400/[0.06] hover:text-slate-200"
                >
                  {suggestion}
                </button>
              ))}
            </div>
          </div>
        ) : (
          <div className="mx-auto max-w-4xl space-y-5">
            {messages.map((message) => (
              <MessageBubble key={message.id} message={message} streaming={false} />
            ))}
            {activeResponse && (
              <MessageBubble
                key={activeResponse.id}
                message={{
                  id: activeResponse.id,
                  role: 'assistant',
                  content: activeResponse.content,
                  citations: activeResponse.citations,
                }}
                streaming
              />
            )}
          </div>
        )}
      </div>

      <footer className="shrink-0 border-t border-white/[0.07] bg-[#070b18]/95 p-3 sm:p-4">
        <div className="mx-auto max-w-4xl rounded-2xl border border-indigo-300/15 bg-indigo-950/25 p-2.5 shadow-[0_18px_55px_-38px_rgba(99,102,241,.9)] focus-within:border-cyan-300/25">
          <textarea
            value={input}
            onChange={(event) => setInput(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter' && !event.shiftKey) {
                event.preventDefault()
                void handleSend()
              }
            }}
            placeholder="描述你想研究、设计或推进的工作…"
            disabled={loading}
            rows={embedded ? 3 : 4}
            className="w-full resize-none bg-transparent px-2 py-1.5 text-sm leading-6 text-slate-100 outline-none placeholder:text-slate-600 disabled:cursor-not-allowed"
            aria-label="向 OmniBase 提问"
          />
          <div className="mt-2 flex flex-wrap items-center justify-between gap-2 border-t border-white/[0.06] pt-2">
            <div className="flex flex-wrap items-center gap-2 text-[8px] text-slate-500">
              <ToolState icon={FileSearch} label="知识上下文" state="开启" />
              <ToolState icon={CheckCircle2} label="引用" state="开启" />
              <ToolState icon={Wrench} label="工具执行" state="关闭" muted />
            </div>
            {loading ? (
              <Button
                type="button"
                size="sm"
                variant="outline"
                onClick={() => abortControllerRef.current?.abort()}
                className="h-9 border-amber-300/20 bg-amber-400/5 text-amber-200 hover:bg-amber-400/10"
              >
                <Square className="mr-2 h-3.5 w-3.5" />
                停止
              </Button>
            ) : (
              <Button
                type="button"
                size="sm"
                onClick={() => void handleSend()}
                disabled={!input.trim()}
                className="h-9 bg-gradient-to-r from-indigo-400 to-cyan-400 px-4 text-slate-950 hover:from-indigo-300 hover:to-cyan-300"
              >
                交给 OmniBase
                <Send className="ml-2 h-3.5 w-3.5" />
              </Button>
            )}
          </div>
        </div>
        <div className="mx-auto mt-2 flex max-w-4xl items-center justify-center gap-1.5 text-center text-[8px] text-slate-600">
          <ShieldCheck className="h-3 w-3" />
          模型回答不会自动执行工具；重要结论请检查引用。
        </div>
      </footer>
    </section>
  )
}

function ToolState({
  icon: Icon,
  label,
  state,
  muted = false,
}: {
  icon: React.ComponentType<{ className?: string }>
  label: string
  state: string
  muted?: boolean
}) {
  return (
    <span
      className={cn(
        'flex items-center gap-1.5 rounded-md border border-white/[0.06] px-2 py-1',
        muted ? 'text-slate-600' : 'text-slate-400',
      )}
    >
      <Icon className="h-3 w-3" />
      {label} · {state}
    </span>
  )
}

const MessageBubble = memo(function MessageBubble({
  message,
  streaming,
}: {
  readonly message: Message
  readonly streaming: boolean
}) {
  const isUser = message.role === 'user'
  return (
    <div className={cn('flex gap-3', isUser && 'flex-row-reverse')}>
      <div
        className={cn(
          'flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border',
          isUser
            ? 'border-cyan-300/15 bg-cyan-400/10 text-cyan-200'
            : 'border-indigo-300/15 bg-indigo-400/10 text-indigo-200',
        )}
      >
        {isUser ? <User className="h-4 w-4" /> : <Sparkles className="h-4 w-4" />}
      </div>
      <div className={cn('max-w-[86%]', isUser && 'text-right')}>
        <div
          className={cn(
            'rounded-2xl px-4 py-3 text-left text-sm leading-6',
            isUser
              ? 'rounded-tr-sm bg-cyan-400/10 text-slate-100 ring-1 ring-cyan-300/10'
              : 'rounded-tl-sm bg-white/[0.045] text-slate-200 ring-1 ring-white/[0.06]',
          )}
        >
          {message.content || (streaming ? '正在组织回答…' : '')}
          {streaming && message.content && (
            <span className="ml-1 inline-block h-4 w-0.5 animate-pulse bg-current align-middle" />
          )}
        </div>
        {!isUser && message.citations && message.citations.length > 0 && (
          <div className="mt-2 flex flex-wrap gap-1.5">
            {message.citations.map((citation) => (
              <Badge
                key={citation.index}
                variant="outline"
                className="border-indigo-300/15 bg-indigo-400/5 text-[9px] text-indigo-200"
              >
                [{citation.index}] {citation.snippet.slice(0, 48)}...
              </Badge>
            ))}
          </div>
        )}
      </div>
    </div>
  )
})
