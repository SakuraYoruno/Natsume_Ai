import React, { memo } from 'react'
import StatusBadge from './StatusBadge.jsx'

export default memo(function ServiceCard({ name, status = 'unknown', latency, detail, icon }) {
  const normalizedStatus = String(status || '').toLowerCase()
  const borderColor = {
    up: 'border-l-green-500',
    healthy: 'border-l-green-500',
    degraded: 'border-l-orange-400',
    down: 'border-l-red-500',
    error: 'border-l-red-500',
    not_tested: 'border-l-blue-400',
    unknown: 'border-l-gray-300',
  }[normalizedStatus] ?? 'border-l-gray-300'

  return (
    <div className={`card-hover border-l-4 p-4 ${borderColor}`}>
      <div className="flex items-start justify-between gap-2">
        <div className="flex min-w-0 items-center gap-2">
          {icon ? <span className="shrink-0 text-gray-400">{icon}</span> : null}
          <span className="truncate font-medium text-gray-800">{name}</span>
        </div>
        <StatusBadge status={status} />
      </div>
      {(latency || detail) ? (
        <div className="mt-2 flex items-center gap-3 text-xs text-gray-500">
          {latency ? <span>延迟 {latency}</span> : null}
          {detail ? <span className="truncate">{detail}</span> : null}
        </div>
      ) : null}
    </div>
  )
})
