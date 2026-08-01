'use client'

import { memo, useEffect, useRef, useState } from 'react'
import { Loader2, Send, Sparkles, User } from 'lucide-react'
import { toast } from 'sonner'
import { ragApi, getApiErrorMessage } from '@/lib/api'
import { createTrailingThrottle, isNearBottom } from '@/lib/chat-performance'
import { takeRagStreamEvents } from '@/lib/rag-stream'
import type { Citation } from '@/lib/types'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'

const MAX_STREAM_DURATION_MS = 120_000
const STREAM_RENDER_INTERVAL_MS = 40
const NEAR_BOTTOM_THRESHOLD_PX = 96

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

export default function ChatPage() {
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
                  {
                    id: assistantId,
                    role: 'assistant',
                    content: answerText,
                    citations,
                  },
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
        {
          id: assistantId,
          role: 'assistant',
          content: `[错误] ${errorText}`,
        },
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

  return (
    <div className="flex h-full flex-col">
      <div className="mb-4">
        <h1 className="flex items-center gap-2 text-2xl font-bold tracking-tight">
          <Sparkles className="h-6 w-6 text-primary" />
          AI 问答
        </h1>
        <p className="mt-1 text-sm text-muted-foreground">
          基于你的知识库回答问题，每个论断都带引用
        </p>
      </div>

      <div ref={scrollRef} onScroll={handleScroll} className="flex-1 space-y-4 overflow-auto pr-2">
        {messages.length === 0 && !activeResponse && (
          <div className="flex h-full flex-col items-center justify-center gap-3 text-center">
            <Sparkles className="h-12 w-12 text-muted-foreground/50" />
            <p className="text-sm text-muted-foreground">输入你的问题，AI 将从知识库中检索并回答</p>
          </div>
        )}
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

      <div className="mt-4 flex gap-2">
        <Input
          value={input}
          onChange={(event) => setInput(event.target.value)}
          placeholder="输入问题..."
          onKeyDown={(event) => event.key === 'Enter' && handleSend()}
          disabled={loading}
          autoFocus
        />
        <Button onClick={handleSend} disabled={loading || !input.trim()}>
          {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}
        </Button>
      </div>
    </div>
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
    <div className={`flex gap-3 ${isUser ? 'flex-row-reverse' : ''}`}>
      <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-muted">
        {isUser ? (
          <User className="h-4 w-4 text-muted-foreground" />
        ) : (
          <Sparkles className="h-4 w-4 text-primary" />
        )}
      </div>
      <div className={`max-w-[80%] ${isUser ? 'text-right' : ''}`}>
        <div
          className={`rounded-lg px-4 py-2 text-sm ${
            isUser ? 'bg-primary text-primary-foreground' : 'bg-muted text-foreground'
          }`}
        >
          {message.content || (streaming ? '...' : '')}
          {streaming && message.content && (
            <span className="ml-1 inline-block h-4 w-0.5 animate-pulse bg-current align-middle" />
          )}
        </div>
        {!isUser && message.citations && message.citations.length > 0 && (
          <div className="mt-2 flex flex-wrap gap-1">
            {message.citations.map((citation) => (
              <Badge key={citation.index} variant="outline" className="text-xs">
                [{citation.index}] {citation.snippet.slice(0, 40)}...
              </Badge>
            ))}
          </div>
        )}
      </div>
    </div>
  )
})
