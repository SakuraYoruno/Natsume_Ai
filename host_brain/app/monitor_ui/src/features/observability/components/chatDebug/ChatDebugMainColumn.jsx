import React from 'react'
import { Sparkles } from 'lucide-react'
import NoticeBanner from '../../../../shared/ui/NoticeBanner.jsx'
import PageHero from '../../../../shared/ui/PageHero.jsx'
import ChatDebugHeader from './ChatDebugHeader.jsx'
import ChatInputBar from './ChatInputBar.jsx'
import ChatTranscriptPanel from './ChatTranscriptPanel.jsx'

export default function ChatDebugMainColumn({
  stats,
  streaming,
  heroMetaItems,
  runtimeCard,
  error,
  chatHistory,
  currentResponse,
  chatEndRef,
  message,
  setMessage,
  onSend,
  onStop,
}) {
  return (
    <div className="flex min-w-0 flex-1 flex-col">
      <ChatDebugHeader stats={stats} />

      <div className="mt-3">
        <PageHero
          icon={Sparkles}
          eyebrow="神经反射实时调试"
          title={streaming ? '当前正在流式反射调试' : '手动发起一次神经反射调试'}
          description="适合联调 SSE 事件流、回复生成、神经节点推进以及请求到 Trace 的跳转联动。"
          gradientClass="from-pink-50 via-white to-blue-50"
          metaItems={heroMetaItems}
        />
      </div>

      {runtimeCard}

      {error ? (
        <div className="mt-3">
          <NoticeBanner variant="error" title="请求失败">{error}</NoticeBanner>
        </div>
      ) : null}

      <ChatTranscriptPanel
        chatHistory={chatHistory}
        currentResponse={currentResponse}
        chatEndRef={chatEndRef}
      />

      <ChatInputBar
        message={message}
        setMessage={setMessage}
        streaming={streaming}
        onSend={onSend}
        onStop={onStop}
      />
    </div>
  )
}
