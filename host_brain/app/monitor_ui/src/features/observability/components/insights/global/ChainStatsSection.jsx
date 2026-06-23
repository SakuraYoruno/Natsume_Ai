import React from 'react'
import { GitBranch } from 'lucide-react'
import EmptyStateCard from '../../../../../shared/ui/EmptyStateCard.jsx'
import InsightSection from '../InsightSection.jsx'
import { useChainStatsQuery } from '../../../diagnosticQueries.js'
import {
  buildChainNodeLatencyRows,
  buildChainStatCards,
} from '../../../insightsGlobalModel.js'
import { SectionQueryMeta, StatCard } from '../insightsGroupShared.jsx'

export default function ChainStatsSection({
  windowMinutes,
  pollingEnabled = false,
  pollingInterval = 30,
  pollingTick = 0,
}) {
  const queryState = useChainStatsQuery({ windowMinutes, pollingEnabled, pollingInterval, pollingTick })
  const { statsData: data } = queryState
  const statCards = buildChainStatCards(data)
  const nodeRows = buildChainNodeLatencyRows(data?.node_stats)

  return (
    <InsightSection icon={GitBranch} title="请求链路统计" color="text-blue-600">
      <SectionQueryMeta queryState={queryState} windowMinutes={windowMinutes} sampleKeys={['total_requests', 'node_stats']} />
      <div className="mb-4 grid grid-cols-2 gap-4 sm:grid-cols-4">
        {statCards.map((card) => (
          <StatCard key={card.label} label={card.label} value={card.value} color={card.color} />
        ))}
      </div>
      <div className="card p-4">
        <div className="mb-3 text-xs font-semibold text-gray-600">各节点平均耗时分布（近 {windowMinutes} 分钟）</div>
        <div className="space-y-2">
          {nodeRows.map((node) => (
            <div key={node.key} className="flex items-center gap-2">
              <div className="w-36 shrink-0 truncate text-xs text-gray-600" title={node.nodeName}>
                {node.nodeName}
              </div>
              <div className="h-2 flex-1 overflow-hidden rounded-full bg-gray-100">
                <div className="h-2 rounded-full transition-all" style={{ width: node.barWidth, backgroundColor: node.barColor, opacity: 0.75 }} />
              </div>
              <div className="w-24 shrink-0 text-right text-xs text-gray-500">
                {node.latencyLabel}
                {node.p95Label ? <span className="text-gray-400">{node.p95Label}</span> : null}
              </div>
              {node.degradedLabel ? <div className="w-16 shrink-0 text-right text-xs text-orange-500">{node.degradedLabel}</div> : null}
              {node.timeoutLabel ? <div className="w-12 shrink-0 text-right text-xs text-red-500">{node.timeoutLabel}</div> : null}
            </div>
          ))}
          {nodeRows.length === 0 && (
            <EmptyStateCard title="暂无节点调用数据" hint="窗口内暂无审计快照记录。" className="p-6" />
          )}
        </div>
      </div>
    </InsightSection>
  )
}
