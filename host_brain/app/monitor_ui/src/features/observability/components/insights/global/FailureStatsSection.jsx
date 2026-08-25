import { TriangleAlert } from 'lucide-react'
import InsightSection from '../InsightSection.jsx'
import { useFailureStatsQuery } from '../../../diagnosticQueries.js'
import {
  buildFailureEventRows,
  buildFailureStatCards,
} from '../../../insightsGlobalModel.js'
import { SectionQueryMeta, StatCard } from '../insightsGroupShared.jsx'

export default function FailureStatsSection({
  windowMinutes,
  pollingEnabled = false,
  pollingInterval = 30,
  pollingTick = 0,
}) {
  const queryState = useFailureStatsQuery({ windowMinutes, pollingEnabled, pollingInterval, pollingTick })
  const { statsData: data } = queryState
  const statCards = buildFailureStatCards(data)
  const eventRows = buildFailureEventRows(data)

  return (
    <InsightSection icon={TriangleAlert} title="失败事件统计" color="text-amber-600">
      <SectionQueryMeta queryState={queryState} windowMinutes={windowMinutes} sampleKeys={['total_failures', 'top_events']} />
      <div className="mb-4 grid grid-cols-2 gap-4 sm:grid-cols-4">
        {statCards.map((card) => (
          <StatCard key={card.label} label={card.label} value={card.value} color={card.color} />
        ))}
      </div>
      {eventRows.length > 0 && (
        <div className="card p-4">
          <div className="mb-3 text-xs font-semibold text-gray-600">失败事件分布</div>
          <div className="space-y-2">
            {eventRows.map((item) => (
              <div key={item.event} className="flex items-center gap-2">
                <div className="w-40 truncate text-xs font-mono text-gray-600">{item.event}</div>
                <div className="h-2 flex-1 rounded-full bg-gray-100">
                  <div className="h-2 rounded-full bg-amber-400" style={{ width: item.barWidth }} />
                </div>
                <div className="w-10 text-right text-xs text-gray-500">{item.count}</div>
                {item.degraded_mode && <div className="w-28 truncate text-xs text-blue-400">{item.degraded_mode}</div>}
              </div>
            ))}
          </div>
        </div>
      )}
    </InsightSection>
  )
}
