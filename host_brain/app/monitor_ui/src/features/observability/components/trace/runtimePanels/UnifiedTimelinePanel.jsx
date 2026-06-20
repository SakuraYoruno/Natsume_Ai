import React, { useMemo, useState } from 'react'
import { Clock3 } from 'lucide-react'
import EmptyStateCard from '../../../../../shared/ui/EmptyStateCard.jsx'
import StatusBadge from '../../../../../shared/ui/StatusBadge.jsx'
import { TraceMiniStat as MiniStat, TraceSectionCard as SectionCard } from '../TraceSurfaces.jsx'
import { buildUnifiedTimelineView } from '../traceUnifiedTimelineModel.js'
import { buildUnifiedTimelineItems, formatDateTime, normalizeStatus } from '../TraceRequestDetailModel.jsx'

export default function UnifiedTimelinePanel({ nodes, tribeEvents, selectedNode, onSelectNode }) {
  const [typeFilter, setTypeFilter] = useState('all')
  const [stageFilter, setStageFilter] = useState('all')
  const [keyword, setKeyword] = useState('')

  const items = useMemo(
    () => buildUnifiedTimelineItems(nodes, tribeEvents),
    [nodes, tribeEvents]
  )
  const { stageOptions, filteredItems } = useMemo(
    () => buildUnifiedTimelineView({ items, typeFilter, stageFilter, keyword }),
    [items, keyword, stageFilter, typeFilter]
  )

  return (
    <SectionCard
      icon={Clock3}
      title="同轴时间线（节点 + Tribe）"
      description="把节点执行与 Tribe 事件放到同一时间轴，快速定位跨层状态衔接是否顺滑。"
      aside={<div className="text-xs text-gray-400">{filteredItems.length} / {items.length} 条</div>}
    >
      <div className="mb-4 grid grid-cols-1 gap-3 sm:grid-cols-3">
        <MiniStat label="节点条目" value={nodes.length} />
        <MiniStat label="Tribe 条目" value={tribeEvents.length} />
        <MiniStat label="最新时间" value={items[0]?.ts_ms ? formatDateTime(items[0].ts_ms) : '-'} />
      </div>

      <div className="mb-4 rounded-xl border border-primary-100/70 bg-white/74 p-3">
        <div className="grid grid-cols-1 gap-3 md:grid-cols-[8rem_12rem_minmax(0,1fr)_auto]">
          <select
            className="w-full rounded-lg border border-primary-100/70 bg-white/88 px-3 py-2 text-sm text-gray-700 focus:border-blue-300 focus:outline-none focus:ring-2 focus:ring-blue-100"
            value={typeFilter}
            onChange={(event) => setTypeFilter(event.target.value)}
          >
            <option value="all">全部类型</option>
            <option value="node">仅节点</option>
            <option value="tribe">仅Tribe</option>
          </select>
          <select
            className="w-full rounded-lg border border-primary-100/70 bg-white/88 px-3 py-2 text-sm text-gray-700 focus:border-blue-300 focus:outline-none focus:ring-2 focus:ring-blue-100"
            value={stageFilter}
            onChange={(event) => setStageFilter(event.target.value)}
          >
            <option value="all">全部阶段</option>
            {stageOptions.map((option) => (
              <option key={option.stageId} value={option.stageId}>{option.label}</option>
            ))}
          </select>
          <input
            className="w-full rounded-lg border border-primary-100/70 bg-white/88 px-3 py-2 text-sm text-gray-700 focus:border-blue-300 focus:outline-none focus:ring-2 focus:ring-blue-100"
            placeholder="搜索标题、节点ID、payload 关键词"
            value={keyword}
            onChange={(event) => setKeyword(event.target.value)}
          />
          <button
            className="btn-secondary text-xs"
            onClick={() => {
              setTypeFilter('all')
              setStageFilter('all')
              setKeyword('')
            }}
          >
            重置
          </button>
        </div>
      </div>

      {filteredItems.length === 0 ? (
        <EmptyStateCard title="暂无可对齐数据" hint="等待节点执行或 Tribe 事件上报后会自动出现。" className="p-4" />
      ) : (
        <div className="space-y-2">
          {filteredItems.map((item) => {
            const stageLabel = item.stageLabel
            const isNode = item.type === 'node'
            const isSelectedNode = isNode && selectedNode?.node_id === item?.node?.node_id
            const containerClassName = isNode
              ? (isSelectedNode ? 'border-blue-200 bg-blue-50' : 'border-primary-100/70 bg-white/88 hover:bg-blue-50/30')
              : 'border-emerald-200 bg-emerald-50/60'
            return (
              <button
                key={item.key}
                className={`w-full rounded-xl border px-3 py-3 text-left transition-colors ${containerClassName}`}
                onClick={() => { if (isNode) onSelectNode?.(item.node) }}
              >
                <div className="flex flex-wrap items-center gap-2 text-xs text-gray-500">
                  <span className={`rounded-full border px-2 py-0.5 ${isNode ? 'border-blue-200 bg-blue-50 text-blue-700' : 'border-emerald-200 bg-emerald-50 text-emerald-700'}`}>
                    {isNode ? 'NODE' : 'TRIBE'}
                  </span>
                  <span className="rounded-full border border-primary-200 bg-primary-50/60 px-2 py-0.5 text-primary-700">{stageLabel}</span>
                  <StatusBadge status={normalizeStatus(item.status)} />
                  <span className="ml-auto">{formatDateTime(item.ts_ms)}</span>
                </div>
                <div className="mt-2 text-sm font-semibold text-gray-900 break-all">{item.title}</div>
                <div className="mt-1 text-xs font-mono text-gray-500 break-all">{item.subtitle}</div>
                {item.preview ? <div className="mt-2 text-sm text-gray-700 break-all">{item.preview}</div> : null}
              </button>
            )
          })}
        </div>
      )}
    </SectionCard>
  )
}
