import React, { useState } from 'react'
import { Search } from 'lucide-react'
import RenderValue from '../../../../../shared/ui/RenderValue.jsx'
import {
  buildTraceGanttRowView,
  buildTraceGanttTicks,
  computeTraceGanttTimeline,
  filterTraceGanttTimeline,
} from '../traceAdvancedViewModel.js'

export default function GanttView({ traceData, onSelectNode }) {
  const timeline = React.useMemo(() => computeTraceGanttTimeline(traceData), [traceData])
  const [filter, setFilter] = useState('')

  if (timeline.length === 0) return null

  const totalMs = timeline[0]?.total_ms ?? 1
  const filtered = filterTraceGanttTimeline(timeline, filter)
  const ticks = buildTraceGanttTicks(totalMs)

  return (
    <div className="h-full overflow-auto bg-white p-4">
      <div className="mb-3 flex items-center justify-between gap-3">
        <span className="text-xs font-medium text-gray-500">节点执行甘特图（总耗时 {totalMs}ms）</span>
        <div className="relative">
          <Search size={11} className="absolute left-2 top-1/2 -translate-y-1/2 text-gray-400" />
          <input
            className="w-36 rounded border border-gray-300 py-1 pl-6 pr-2 text-xs focus:outline-none focus:ring-1 focus:ring-blue-200"
            placeholder="过滤节点…"
            value={filter}
            onChange={(event) => setFilter(event.target.value)}
          />
        </div>
      </div>
      <div className="mb-2 flex pl-40">
        {ticks.map((tick, index) => (
          <div key={index} className="flex-1 border-l border-gray-200 text-center text-xs text-gray-400 first:border-l-0">
            {tick}ms
          </div>
        ))}
      </div>
      <div className="space-y-1.5">
        {filtered.map((node) => {
          const row = buildTraceGanttRowView(node, totalMs)
          return (
            <div
              key={node.node_id}
              className="flex cursor-pointer items-center gap-2 rounded border border-gray-200 bg-gray-50 transition-shadow hover:shadow-sm"
              style={{ padding: '4px 8px' }}
              onClick={() => onSelectNode?.(node)}
              title={row.title}
            >
              <div className="w-36 shrink-0">
                <div className="truncate text-xs font-medium text-gray-700">{row.displayName}</div>
                <div className="text-xs text-gray-400">{row.latencyLabel}</div>
              </div>
              <div className="relative h-6 flex-1 overflow-hidden rounded bg-gray-100">
                <div
                  className="absolute top-0 h-full rounded"
                  title={row.latencyLabel}
                  style={row.barStyle}
                />
              </div>
              <span className={`w-10 shrink-0 text-right text-xs font-medium ${row.statusClassName}`}>
                <RenderValue value={node.status ?? '—'} />
              </span>
            </div>
          )
        })}
      </div>
    </div>
  )
}
