import React from 'react'
import RepresentativeRequests from '../../../../shared/ui/observability/RepresentativeRequests.jsx'
import GatewayDiagnosticsPanel from './GatewayDiagnosticsPanel.jsx'
import GatewayHotspotsPanel from './GatewayHotspotsPanel.jsx'
import GatewayRemediationSteps from './GatewayRemediationSteps.jsx'

export default function GatewayRemediationPanel({ model, view }) {
  const {
    humanoidLoopRemediationData,
    humanoidLoopDiagnosticsData,
    humanoidLoopHotspotsData,
    humanoidLoopHotspotTrendData,
    humanoidLoopSourceCorrelationData,
  } = model
  const {
    humanoidAlertStatus,
    humanoidRepresentativeRefs,
    humanoidDiagnostics,
    humanoidHotspots,
    humanoidHotspotTrendLabels,
    humanoidHotspotTrendSeries,
    humanoidHotspotTrendBuckets,
    humanoidSourceItems,
    humanoidRemediationSteps,
  } = view

  if (!humanoidLoopRemediationData) return null

  return (
    <div className="mt-4 card p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="text-xs font-semibold text-gray-600">闭环自愈建议</div>
          <div className="mt-1 text-sm text-gray-700">
            {humanoidLoopRemediationData?.summary || '当前暂无额外排查建议。'}
          </div>
        </div>
        <span
          className={`rounded-full border px-2.5 py-1 text-xs font-medium ${
            humanoidAlertStatus === 'critical'
              ? 'border-red-200 bg-red-50 text-red-700'
              : humanoidAlertStatus === 'degraded'
                ? 'border-orange-200 bg-orange-50 text-orange-700'
                : 'border-emerald-200 bg-emerald-50 text-emerald-700'
          }`}
        >
          {humanoidLoopRemediationData?.primary_focus || '维持稳定'}
        </span>
      </div>

      {humanoidLoopRemediationData?.next_review_hint ? (
        <div className="mt-3 rounded-xl border border-primary-100/70 bg-primary-50/55 px-3 py-2 text-xs text-primary-700">
          {humanoidLoopRemediationData.next_review_hint}
        </div>
      ) : null}

      <div className="mt-4">
        <RepresentativeRequests
          title="快速钻取请求"
          hint="优先打开一条代表性 request，把闭环异常还原到主链路。"
          items={humanoidRepresentativeRefs}
          emptyTitle="暂无代表性 request"
          emptyHint="等异常样本更稳定后，这里会给出直达 Trace 的代表性 request。"
        />
      </div>

      <GatewayHotspotsPanel
        humanoidLoopHotspotsData={humanoidLoopHotspotsData}
        humanoidLoopHotspotTrendData={humanoidLoopHotspotTrendData}
        humanoidLoopSourceCorrelationData={humanoidLoopSourceCorrelationData}
        humanoidHotspots={humanoidHotspots}
        humanoidHotspotTrendLabels={humanoidHotspotTrendLabels}
        humanoidHotspotTrendSeries={humanoidHotspotTrendSeries}
        humanoidHotspotTrendBuckets={humanoidHotspotTrendBuckets}
        humanoidSourceItems={humanoidSourceItems}
      />

      <GatewayDiagnosticsPanel
        humanoidLoopDiagnosticsData={humanoidLoopDiagnosticsData}
        humanoidDiagnostics={humanoidDiagnostics}
      />

      <GatewayRemediationSteps steps={humanoidRemediationSteps} />
    </div>
  )
}
