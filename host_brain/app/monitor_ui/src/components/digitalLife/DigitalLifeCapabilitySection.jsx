import React from 'react'
import EmptyStateCard from '../EmptyStateCard.jsx'
import MetricCard from '../MetricCard.jsx'
import NoticeBanner from '../NoticeBanner.jsx'
import { LifeSection } from './digitalLifeSectionShared.jsx'

export default function DigitalLifeCapabilitySection({ model }) {
  const {
    capabilityGapData,
    forgeQualitySummary,
    avgForgeHealth,
    forgeCriticalCount,
    forgeUnhealthyCount,
    forgeRiskItems,
  } = model

  return (
    <LifeSection
      title="Capability Gap & Forge Loop"
      hint="Expose gap pressure and forge runtime quality directly in the life-body overview."
      actionTo="/insights/global"
      actionLabel="Open Global Insights"
    >
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <MetricCard label="Gap Detected" value={capabilityGapData?.detected_total ?? '--'} hint={`failed ${capabilityGapData?.failed_total ?? 0}`} tone="text-indigo-600" />
        <MetricCard label="Forge Candidates" value={(capabilityGapData?.forge_candidates ?? []).length} hint={`runtime caps ${capabilityGapData?.active_runtime_capabilities ?? 0}`} tone="text-blue-600" />
        <MetricCard label="Avg Health" value={(forgeQualitySummary?.tracked_proposals ?? 0) > 0 ? avgForgeHealth.toFixed(1) : '--'} hint={`tracked ${forgeQualitySummary?.tracked_proposals ?? 0}`} tone={avgForgeHealth >= 70 ? 'text-green-600' : 'text-orange-500'} />
        <MetricCard label="Risk Proposals" value={forgeCriticalCount} hint={`unhealthy ${forgeUnhealthyCount}`} tone={forgeCriticalCount > 0 ? 'text-red-600' : forgeUnhealthyCount > 0 ? 'text-orange-500' : 'text-green-600'} />
      </div>

      {forgeCriticalCount > 0 ? (
        <div className="mt-4">
          <NoticeBanner variant="error" title="Forge has critical proposals">
            {`critical=${forgeCriticalCount}, unhealthy=${forgeUnhealthyCount}, avg_health=${avgForgeHealth.toFixed(1)}`}
          </NoticeBanner>
        </div>
      ) : forgeUnhealthyCount > 0 ? (
        <div className="mt-4">
          <NoticeBanner variant="warning" title="Forge has unhealthy proposals">
            {`unhealthy=${forgeUnhealthyCount}, avg_health=${avgForgeHealth.toFixed(1)}`}
          </NoticeBanner>
        </div>
      ) : null}

      <div className="mt-4 grid grid-cols-1 gap-4 xl:grid-cols-2">
        <div className="card p-4">
          <div className="mb-2 text-xs font-semibold text-gray-600">Gap Hotspots</div>
          {(capabilityGapData?.hotspots ?? []).length > 0 ? (
            <div className="space-y-2">
              {(capabilityGapData?.hotspots ?? []).slice(0, 5).map((item) => (
                <div key={item.candidate_key} className="rounded-lg border border-primary-100/70 bg-white/80 px-3 py-2 text-xs">
                  <div className="flex items-center justify-between gap-2">
                    <span className="font-mono text-orange-600">{item.reason_code}</span>
                    <span className="text-gray-500">{`fail ${(Number(item.failure_rate || 0) * 100).toFixed(1)}%`}</span>
                  </div>
                  <div className="mt-1 text-gray-500">
                    {`family=${item.suggested_family} | detected=${item.detected_count} | failed=${item.failed_count}`}
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <EmptyStateCard title="暂无能力缺口热点" hint="缺口事件积累后会展示高压原因与失败比例。" className="p-4" />
          )}
        </div>

        <div className="card p-4">
          <div className="mb-2 text-xs font-semibold text-gray-600">Forge Risk Queue</div>
          {forgeRiskItems.length > 0 ? (
            <div className="space-y-2">
              {forgeRiskItems.map((item) => {
                const health = Number(item.health_score || 0)
                const barClass = health >= 75 ? 'bg-green-400' : health >= 55 ? 'bg-orange-400' : 'bg-red-400'
                return (
                  <div key={item.proposal_id} className="rounded-lg border border-primary-100/70 bg-white/80 px-3 py-2 text-xs">
                    <div className="flex items-center justify-between gap-2">
                      <span className="font-mono text-indigo-600">{(item.proposal_id || '').slice(0, 18)}</span>
                      <span className="text-gray-500">{String(item.stage || '').toUpperCase()}</span>
                    </div>
                    <div className="mt-1 flex items-center gap-2">
                      <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-gray-100">
                        <div className={`h-full rounded-full ${barClass}`} style={{ width: `${Math.max(3, Math.min(100, health))}%` }} />
                      </div>
                      <span className="text-gray-600">{health.toFixed(1)}</span>
                    </div>
                    <div className="mt-1 text-gray-500">
                      {`fail ${(Number(item.window_failure_rate || 0) * 100).toFixed(1)}% | p95 ${item.window_p95_latency_ms ?? 0}ms | tune ${item.last_tune_action || 'NONE'}`}
                    </div>
                  </div>
                )
              })}
            </div>
          ) : (
            <EmptyStateCard title="暂无 Forge 运行质量记录" hint="提案执行后会展示健康度、失败率与调优动作。" className="p-4" />
          )}
        </div>
      </div>
    </LifeSection>
  )
}
