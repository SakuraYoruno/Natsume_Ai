import React from 'react'
import { GitBranch } from 'lucide-react'
import EmptyStateCard from '../../../../../shared/ui/EmptyStateCard.jsx'
import NoticeBanner from '../../../../../shared/ui/NoticeBanner.jsx'
import InsightSection from '../InsightSection.jsx'
import { useCapabilityGapStatsQuery } from '../../../diagnosticQueries.js'
import {
  buildCapabilityGapHotspotRows,
  buildCapabilityGapStatCards,
  buildForgeQualityStatCards,
  buildForgeRuntimeQualityRows,
} from '../../../insightsGlobalModel.js'
import { SectionQueryMeta, StatCard } from '../insightsGroupShared.jsx'

export default function CapabilityGapStatsSection({
  windowMinutes,
  highlightForgeRisk = false,
  pollingEnabled = false,
  pollingInterval = 30,
  pollingTick = 0,
}) {
  const queryState = useCapabilityGapStatsQuery({ windowMinutes, pollingEnabled, pollingInterval, pollingTick })
  const { statsData: data } = queryState
  const projectionUnavailable = Boolean(queryState.queryError && !data)

  const qualitySummary = data?.forge_runtime_quality?.summary || {}
  const qualityItems = data?.forge_runtime_quality?.items || []
  const gapStatCards = buildCapabilityGapStatCards(data)
  const forgeQualityStatCards = buildForgeQualityStatCards(qualitySummary)
  const hotspotRows = buildCapabilityGapHotspotRows(data)
  const forgeQualityRows = buildForgeRuntimeQualityRows(qualityItems)

  return (
    <div id="forge-runtime-risk" className={highlightForgeRisk ? 'rounded-2xl ring-2 ring-red-200/80 ring-offset-2 ring-offset-white' : ''}>
      {highlightForgeRisk ? (
        <NoticeBanner variant="warning" title="Forge 风险焦点模式">
          当前视图由全局告警跳转而来，已定位到 Capability Gap / Forge Runtime 细分面板。
        </NoticeBanner>
      ) : null}
      <InsightSection icon={GitBranch} title="Capability Gap & Forge Quality" color="text-indigo-600">
        <SectionQueryMeta queryState={queryState} windowMinutes={windowMinutes} sampleKeys={['detected_total', 'hotspots', 'forge_runtime_quality']} />
        {projectionUnavailable ? (
          <NoticeBanner variant="warning" title="Capability Forge projection unavailable">
            {queryState.queryError || 'OBS_CAPABILITY_FORGE_PROJECTION_UNAVAILABLE'}
          </NoticeBanner>
        ) : (
          <>
            <div className="mb-4 grid grid-cols-2 gap-4 sm:grid-cols-4">
              {gapStatCards.map((card) => (
                <StatCard key={card.label} label={card.label} value={card.value} color={card.color} />
              ))}
            </div>

            <div className="mb-4 grid grid-cols-2 gap-4 sm:grid-cols-4">
              {forgeQualityStatCards.map((card) => (
                <StatCard key={card.label} label={card.label} value={card.value} color={card.color} />
              ))}
            </div>

            <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
              <div className="card p-4">
                <div className="mb-3 text-xs font-semibold text-gray-600">Capability Gap Hotspots</div>
                {hotspotRows.length > 0 ? (
                  <div className="space-y-2">
                    {hotspotRows.map((item) => (
                      <div key={item.candidate_key} className="rounded-lg border border-primary-100/70 bg-white/80 px-3 py-2 text-xs">
                        <div className="flex items-center justify-between gap-2">
                          <span className="font-mono text-orange-600">{item.reason_code}</span>
                          <span className="text-gray-500">{item.failureRateLabel}</span>
                        </div>
                        <div className="mt-1 text-gray-500">
                          {item.detail}
                        </div>
                      </div>
                    ))}
                  </div>
                ) : (
                  <EmptyStateCard title="暂无能力缺口热点" hint="等待缺口事件积累后展示高压候选。" className="p-5" />
                )}
              </div>

              <div className="card p-4">
                <div className="mb-3 text-xs font-semibold text-gray-600">Forge Runtime Quality (Worst First)</div>
                {forgeQualityRows.length > 0 ? (
                  <div className="space-y-2">
                    {forgeQualityRows.map((item) => (
                      <div key={item.key} className="rounded-lg border border-primary-100/70 bg-white/80 px-3 py-2 text-xs">
                        <div className="flex items-center justify-between gap-2">
                          <span className="font-mono text-indigo-600">{item.proposalLabel}</span>
                          <span className="text-gray-500">{item.stageLabel}</span>
                        </div>
                        <div className="mt-1 flex items-center gap-2">
                          <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-gray-100">
                            <div className={`h-full rounded-full ${item.healthBarClass}`} style={{ width: item.healthBarWidth }} />
                          </div>
                          <span className="text-gray-600">{item.healthLabel}</span>
                        </div>
                        <div className="mt-1 text-gray-500">
                          {item.detail}
                        </div>
                      </div>
                    ))}
                  </div>
                ) : (
                  <EmptyStateCard title="暂无 Forge 质量记录" hint="尚未观察到提案运行结果。" className="p-5" />
                )}
              </div>
            </div>
          </>
        )}
      </InsightSection>
    </div>
  )
}
