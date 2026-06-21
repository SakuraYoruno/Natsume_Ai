import React from 'react'
import { ShieldAlert } from 'lucide-react'
import InsightSection from '../InsightSection.jsx'
import { useUiAssertionStatsQuery } from '../../../diagnosticQueries.js'
import {
  buildUiAssertionReasonRows,
  buildUiAssertionStatCards,
} from '../../../insightsSafetyModel.js'
import { SectionQueryMeta, StatCard } from '../insightsGroupShared.jsx'

export default function UiAssertionSection({
  windowMinutes,
  pollingEnabled = false,
  pollingInterval = 30,
  pollingTick = 0,
}) {
  const queryState = useUiAssertionStatsQuery({ windowMinutes, pollingEnabled, pollingInterval, pollingTick })
  const { statsData: data } = queryState
  const statCards = buildUiAssertionStatCards(data)
  const reasonRows = buildUiAssertionReasonRows(data)

  return (
    <InsightSection icon={ShieldAlert} title="UI 断言安全统计" color="text-red-600">
      <SectionQueryMeta queryState={queryState} windowMinutes={windowMinutes} sampleKeys={['total_assertions', 'precondition_failed', 'top_failed_reasons']} />
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 mb-4">
        {statCards.map((card) => (
          <StatCard key={card.label} {...card} />
        ))}
      </div>
      {reasonRows.length > 0 && (
        <div className="card p-4">
          <div className="text-xs font-semibold text-gray-600 mb-3">前置断言失败原因分布</div>
          <div className="space-y-1.5">
            {reasonRows.map((r) => (
              <div key={r.key} className="flex items-center gap-2">
                <div className="text-xs font-mono text-gray-600 w-36 truncate">{r.reason}</div>
                <div className="flex-1 bg-gray-100 rounded-full h-1.5">
                  <div className="bg-red-400 h-1.5 rounded-full" style={{ width: r.barWidth }} />
                </div>
                <div className="text-xs text-gray-500 w-8 text-right">{r.count}</div>
              </div>
            ))}
          </div>
        </div>
      )}
    </InsightSection>
  )
}
