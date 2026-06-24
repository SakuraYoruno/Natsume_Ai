import React from 'react'
import { AlertTriangle, Send } from 'lucide-react'

export default function ChatInputBar({
  message,
  setMessage,
  streaming,
  onSend,
  onStop,
}) {
  return (
    <div className="mt-3 flex flex-col gap-2 rounded-xl border border-white/80 bg-white/86 p-3 shadow-sm sm:flex-row sm:items-end">
      <textarea
        rows={3}
        className="min-h-[5.75rem] flex-1 resize-y rounded-lg border border-gray-300 px-3 py-2.5 text-sm leading-6 focus:outline-none focus:ring-2 focus:ring-primary-200"
        aria-label="聊天调试输入"
        placeholder="输入一条用于调试的消息"
        value={message}
        onChange={(event) => setMessage(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === 'Enter' && !event.shiftKey) {
            event.preventDefault()
            onSend()
          }
        }}
        disabled={streaming}
      />
      {streaming ? (
        <button className="btn-secondary justify-center px-4 sm:min-h-[2.75rem]" onClick={onStop}>
          <AlertTriangle size={14} />
          停止
        </button>
      ) : (
        <button className="btn-primary justify-center px-4 sm:min-h-[2.75rem]" onClick={onSend} disabled={!message.trim()}>
          <Send size={14} />
          发送
        </button>
      )}
    </div>
  )
}
