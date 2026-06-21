import React from 'react'
import EmptyStateCard from '../../../../shared/ui/EmptyStateCard.jsx'
import { formatDateTime } from '../../../../shared/formatters.js'

const GATEWAY_TIME_OPTIONS = Object.freeze({
  month: '2-digit',
  day: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
  second: '2-digit',
})

export default function GatewayWeChatDetailCard({
  wechatDetail,
  hasWeChatRateLimits,
  hasWeChatEvents,
  resetting,
  resetMsg,
  onResetWeChat,
}) {
  if (!wechatDetail) return null

  return (
    <div className="card space-y-4 p-4">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold text-gray-700">微信通道详情</h2>
        <button className="btn-secondary text-xs" onClick={onResetWeChat} disabled={resetting}>
          {resetting ? '重置中…' : '重置频率限制'}
        </button>
      </div>

      {resetMsg ? (
        <div className={`rounded px-3 py-2 text-xs ${resetMsg.ok ? 'bg-green-50 text-green-700' : 'bg-red-50 text-red-700'}`}>
          {resetMsg.text}
        </div>
      ) : null}

      {hasWeChatRateLimits ? (
        <div className="space-y-1">
          <div className="text-xs font-medium text-gray-600">频率限制</div>
          {wechatDetail.rate_limits.map((item, index) => (
            <div key={`${item.key ?? item.name ?? 'limit'}-${index}`} className="flex items-center justify-between rounded bg-gray-50 px-3 py-1.5 text-xs">
              <span className="text-gray-600">{item.key || item.name || `限制 ${index + 1}`}</span>
              <span className={(item.remaining ?? 0) <= 0 ? 'font-medium text-red-500' : 'text-gray-500'}>
                {item.current}/{item.limit}（剩余 {item.remaining}）
              </span>
            </div>
          ))}
        </div>
      ) : (
        <EmptyStateCard title="当前窗口没有频率限制命中记录" hint="说明节流状态稳定。" className="p-4" />
      )}

      {hasWeChatEvents ? (
        <div className="space-y-1">
          <div className="text-xs font-medium text-gray-600">最近事件</div>
          <div className="max-h-48 space-y-1 overflow-y-auto">
            {wechatDetail.recent_events.slice(0, 15).map((event, index) => (
              <div key={`event-${index}`} className="flex items-center gap-2 rounded bg-gray-50 px-3 py-1.5 text-xs">
                <span className="shrink-0 text-gray-400">{formatDateTime(event.ts_ms, { options: GATEWAY_TIME_OPTIONS })}</span>
                <span className="truncate font-mono text-gray-600">{event.event_type || event.audit_tag || '—'}</span>
              </div>
            ))}
          </div>
        </div>
      ) : (
        <EmptyStateCard title="当前窗口暂无微信事件" hint="触发一条消息后会自动展示。" className="p-4" />
      )}
    </div>
  )
}
