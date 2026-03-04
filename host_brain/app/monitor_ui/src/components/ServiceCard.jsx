import React from 'react'
import StatusBadge from './StatusBadge.jsx'

/**
 * 服务健康卡片
 * @param {object} props
 * @param {string} props.name - 服务名称（display_name）
 * @param {'UP'|'DEGRADED'|'DOWN'|'UNKNOWN'|'healthy'|'degraded'|'error'|'unknown'} props.status
 * @param {string} [props.latency] - 延迟，如 "12ms"（p95_ms）
 * @param {string} [props.detail] - 附加说明（call_count）
 * @param {React.ReactNode} [props.icon] - Lucide 图标
 */
export default function ServiceCard({ name, status = 'unknown', latency, detail, icon }) {
  // 兼容后端大写（UP/DEGRADED/DOWN/UNKNOWN）和前端小写（healthy/degraded/error/unknown）
  const normalizedStatus = status?.toLowerCase()
  const borderColor = {
    up:       'border-l-green-500',
    healthy:  'border-l-green-500',
    degraded: 'border-l-orange-400',
    down:     'border-l-red-500',
    error:    'border-l-red-500',
    unknown:  'border-l-gray-300',
  }[normalizedStatus] ?? 'border-l-gray-300'

  return (
    <div className={`card-hover p-4 border-l-4 ${borderColor}`}>
      <div className="flex items-start justify-between gap-2">
        <div className="flex items-center gap-2 min-w-0">
          {icon && (
            <span className="text-gray-400 shrink-0">{icon}</span>
          )}
          <span className="font-medium text-gray-800 truncate">{name}</span>
        </div>
        <StatusBadge status={status} />
      </div>
      {(latency || detail) && (
        <div className="mt-2 flex items-center gap-3 text-xs text-gray-500">
          {latency && <span>延迟 {latency}</span>}
          {detail  && <span className="truncate">{detail}</span>}
        </div>
      )}
    </div>
  )
}
