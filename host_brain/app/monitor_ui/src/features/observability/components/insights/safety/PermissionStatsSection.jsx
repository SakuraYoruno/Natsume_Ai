import React from 'react'
import { Lock } from 'lucide-react'
import InsightSection from '../InsightSection.jsx'
import { usePermissionStatsQuery } from '../../../diagnosticQueries.js'
import {
  buildPermissionDeniedRows,
  buildPermissionStatCards,
} from '../../../insightsSafetyModel.js'
import { SectionQueryMeta, StatCard } from '../insightsGroupShared.jsx'

export default function PermissionStatsSection({
  windowMinutes,
  pollingEnabled = false,
  pollingInterval = 30,
  pollingTick = 0,
}) {
  const queryState = usePermissionStatsQuery({ windowMinutes, pollingEnabled, pollingInterval, pollingTick })
  const { statsData: data } = queryState
  const statCards = buildPermissionStatCards(data)
  const deniedRows = buildPermissionDeniedRows(data)

  return (
    <InsightSection icon={Lock} title="权限拒绝统计" color="text-violet-600">
      <SectionQueryMeta queryState={queryState} windowMinutes={windowMinutes} sampleKeys={['total_checks', 'denied_count', 'top_denied_tools']} />
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 mb-4">
        {statCards.map((card) => (
          <StatCard key={card.label} {...card} />
        ))}
      </div>
      {deniedRows.length > 0 && (
        <div className="card p-4">
          <div className="text-xs font-semibold text-gray-600 mb-3">拒绝工具 Top</div>
          <div className="space-y-2">
            {deniedRows.map((t) => (
              <div key={t.key} className="flex items-center gap-2">
                <div className="text-xs font-mono text-gray-600 w-36 truncate">{t.tool_name}</div>
                <div className="flex-1 bg-gray-100 rounded-full h-2">
                  <div className="bg-violet-400 h-2 rounded-full" style={{ width: t.barWidth }} />
                </div>
                <div className="text-xs text-gray-500 w-8 text-right">{t.deny_count}</div>
                {t.proposer_class && <div className="text-xs text-gray-400 w-20 truncate">{t.proposer_class}</div>}
              </div>
            ))}
          </div>
        </div>
      )}
    </InsightSection>
  )
}
