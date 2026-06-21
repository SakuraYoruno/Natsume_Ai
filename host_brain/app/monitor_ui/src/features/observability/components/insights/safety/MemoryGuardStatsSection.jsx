import React from 'react'
import { BookLock } from 'lucide-react'
import InsightSection from '../InsightSection.jsx'
import { useMemoryGuardStatsQuery } from '../../../diagnosticQueries.js'
import {
  buildMemoryGuardSecondaryStatCards,
  buildMemoryGuardStatCards,
  buildMemoryRejectReasonRows,
} from '../../../insightsSafetyModel.js'
import { SectionQueryMeta, StatCard } from '../insightsGroupShared.jsx'

export default function MemoryGuardStatsSection({
  windowMinutes,
  pollingEnabled = false,
  pollingInterval = 30,
  pollingTick = 0,
}) {
  const queryState = useMemoryGuardStatsQuery({ windowMinutes, pollingEnabled, pollingInterval, pollingTick })
  const { statsData: data } = queryState
  const statCards = buildMemoryGuardStatCards(data)
  const secondaryStatCards = buildMemoryGuardSecondaryStatCards(data)
  const rejectReasonRows = buildMemoryRejectReasonRows(data)

  return (
    <InsightSection icon={BookLock} title="记忆守卫校验统计" color="text-emerald-600">
      <SectionQueryMeta queryState={queryState} windowMinutes={windowMinutes} sampleKeys={['total_checks', 'blocked_count', 'top_reject_reasons']} />
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 mb-4">
        {statCards.map((card) => (
          <StatCard key={card.label} {...card} />
        ))}
      </div>
      <div className="grid grid-cols-3 gap-4 mb-4">
        {secondaryStatCards.map((card) => (
          <StatCard key={card.label} {...card} />
        ))}
      </div>
      {rejectReasonRows.length > 0 && (
        <div className="card p-4">
          <div className="text-xs font-semibold text-gray-600 mb-3">拒绝原因分布</div>
          <div className="space-y-2">
            {rejectReasonRows.map((r) => (
              <div key={r.key} className="flex items-center gap-2">
                <div className="text-xs font-mono text-gray-600 w-44 truncate">{r.reason}</div>
                <div className="flex-1 bg-gray-100 rounded-full h-2">
                  <div className="bg-emerald-400 h-2 rounded-full" style={{ width: r.barWidth }} />
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
