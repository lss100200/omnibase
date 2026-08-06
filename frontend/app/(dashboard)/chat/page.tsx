'use client'

import { AIConversationWorkbench } from '@/components/ai/ai-conversation-workbench'

export default function ChatPage() {
  return (
    <div className="h-[calc(100vh-7.5rem)] min-h-[38rem]">
      <AIConversationWorkbench className="h-full" contextLabel="当前租户知识库 · 独立会话" />
    </div>
  )
}
