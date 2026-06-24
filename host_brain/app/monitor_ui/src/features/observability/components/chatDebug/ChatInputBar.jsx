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
    <div className="mt-3 flex gap-2">
      <input
        className="flex-1 rounded-lg border border-gray-300 px-3 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-primary-200"
        aria-label="聊天调试输入"
        placeholder="输入消息，按 Enter 发送"
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
        <button className="btn-secondary px-4" onClick={onStop}>
          <AlertTriangle size={14} />
          停止
        </button>
      ) : (
        <button className="btn-primary px-4" onClick={onSend} disabled={!message.trim()}>
          <Send size={14} />
          发送
        </button>
      )}
    </div>
  )
}
