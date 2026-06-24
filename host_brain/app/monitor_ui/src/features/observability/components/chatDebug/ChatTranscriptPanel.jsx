import React from 'react'
import { AlertTriangle, Bot, Loader2, User } from 'lucide-react'
import EmptyStateCard from '../../../../shared/ui/EmptyStateCard.jsx'

export default function ChatTranscriptPanel({ chatHistory, currentResponse, chatEndRef }) {
  return (
    <div className="card mt-3 min-h-[22rem] space-y-3 overflow-y-auto p-4 xl:max-h-[42rem]">
      {chatHistory.length === 0 ? (
        <EmptyStateCard
          title="发一条消息开始调试"
          hint="右侧会实时展示 SSE 事件、链路节点和 Trace 跳转入口。"
          icon={Bot}
          className="py-12"
        />
      ) : null}

      {chatHistory.map((item, index) => (
        <div key={`${item.role}-${index}`} className={`flex gap-2 ${item.role === 'user' ? 'justify-end' : ''}`}>
          {item.role !== 'user' ? (
            <div className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-full ${item.role === 'error' ? 'bg-red-100' : 'bg-primary-100'}`}>
              {item.role === 'error'
                ? <AlertTriangle size={14} className="text-red-500" />
                : <Bot size={14} className="text-primary" />}
            </div>
          ) : null}

          <div
            className={`max-w-[min(42rem,86%)] rounded-lg px-3 py-2 text-sm ${
              item.role === 'user'
                ? 'bg-primary text-white'
                : item.role === 'error'
                  ? 'border border-red-200 bg-red-50 text-red-700'
                  : 'bg-gray-100 text-gray-800'
            }`}
          >
            <pre className="whitespace-pre-wrap font-sans">{item.content}</pre>
          </div>

          {item.role === 'user' ? (
            <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-gray-200">
              <User size={14} className="text-gray-600" />
            </div>
          ) : null}
        </div>
      ))}

      {currentResponse ? (
        <div className="flex gap-2">
          <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-primary-100">
            <Loader2 size={14} className="animate-spin text-primary" />
          </div>
          <div className="rounded-lg bg-gray-100 px-3 py-2 text-sm text-gray-800">
            <pre className="whitespace-pre-wrap font-sans">{currentResponse}<span className="animate-pulse">▌</span></pre>
          </div>
        </div>
      ) : null}
      <div ref={chatEndRef} />
    </div>
  )
}
