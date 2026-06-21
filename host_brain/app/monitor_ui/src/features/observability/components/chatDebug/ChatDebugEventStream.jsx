import React from 'react'
import { Sparkles } from 'lucide-react'
import EmptyStateCard from '../../../../shared/ui/EmptyStateCard.jsx'
import { formatTime } from '../../../../shared/formatters.js'
import {
  formatAlertTitle,
  formatDecision,
  formatEventPhase,
  formatSceneType,
  humanizeToken,
} from '../../../../utils/digitalLife.js'

function ChatDebugPhaseBadge({ phase }) {
  const styles = {
    PRESENCE: 'bg-blue-100 text-blue-700',
    COMMIT: 'bg-green-100 text-green-700',
    REJECT: 'bg-red-100 text-red-700',
    CHUNK: 'bg-purple-100 text-purple-700',
    NODE: 'bg-orange-100 text-orange-700',
  }

  return (
    <span className={`rounded px-1.5 py-0.5 text-xs font-medium ${styles[phase] || 'bg-gray-100 text-gray-600'}`}>
      {formatEventPhase(phase)}
    </span>
  )
}

function ChatDebugEventRow({ event, index }) {
  const ts = formatTime(event.ts_ms, { placeholder: `#${index + 1}` })
  const phase = String(event.phase || event.event || '?').toUpperCase()

  return (
    <div className={`rounded-lg px-2 py-2 text-xs ${index % 2 === 0 ? 'bg-gray-50' : 'bg-white'}`}>
      <div className="flex items-center gap-2">
        <span className="w-20 shrink-0 text-gray-400">{ts}</span>
        <ChatDebugPhaseBadge phase={phase} />
        {event.node_id ? <span className="rounded bg-indigo-50 px-1.5 py-0.5 text-indigo-600">{event.node_id}</span> : null}
      </div>
      <div className="mt-1 break-words text-gray-600">
        {event.event ? <span className="font-medium text-gray-800">{humanizeToken(event.event)} </span> : null}
        {event.scene_type ? <span>场景：{formatSceneType(event.scene_type)} </span> : null}
        {event.decision ? <span>决策：{formatDecision(event.decision)} </span> : null}
        {event.reason_code ? <span>原因：{formatAlertTitle(event.reason_code)} </span> : null}
        {event.content ? <span>{event.content.slice(0, 120)}</span> : null}
        {event.token ? <span>{event.token}</span> : null}
        {event.raw ? <span>{event.raw}</span> : null}
      </div>
    </div>
  )
}

export default function ChatDebugEventStream({ sseEvents, eventsEndRef }) {
  return (
    <div className="card flex-1 overflow-y-auto p-3">
      <div className="mb-2 flex items-center justify-between">
        <div className="text-sm font-semibold text-gray-800">
          <Sparkles size={14} className="mr-1 inline" />
          实时事件流
        </div>
        <span className="text-xs text-gray-400">{sseEvents.length} 条</span>
      </div>
      {sseEvents.length > 0 ? (
        <div className="space-y-1.5">
          {sseEvents.map((event, index) => (
            <ChatDebugEventRow key={`${event.ts_ms || index}-${index}`} event={event} index={index} />
          ))}
          <div ref={eventsEndRef} />
        </div>
      ) : (
        <EmptyStateCard
          title="等待聊天请求"
          hint="发起请求后，这里会展示数字生命体神经链路、决策与流式事件。"
          className="p-6"
        />
      )}
    </div>
  )
}
