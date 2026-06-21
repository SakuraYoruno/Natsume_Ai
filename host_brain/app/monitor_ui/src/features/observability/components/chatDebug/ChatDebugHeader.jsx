import React from 'react'
import { Clock, Zap } from 'lucide-react'

export default function ChatDebugHeader({ stats }) {
  return (
    <div className="mb-3 flex items-center justify-between gap-3">
      <div>
        <h1 className="text-xl font-semibold text-gray-900">神经反射调试</h1>
        <p className="text-xs text-gray-500">直接发消息，实时看反射事件流、请求 ID 和神经链路入口。</p>
      </div>
      {stats ? (
        <div className="flex items-center gap-3 text-xs text-gray-500">
          <span><Clock size={11} className="mr-1 inline" />{stats.elapsed}ms</span>
          <span><Zap size={11} className="mr-1 inline" />{stats.eventCount} 事件</span>
        </div>
      ) : null}
    </div>
  )
}
