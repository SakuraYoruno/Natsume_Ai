import React from 'react'
import GatewayHotspotCards from './GatewayHotspotCards.jsx'
import GatewayHotspotTrendPanel from './GatewayHotspotTrendPanel.jsx'
import GatewaySourceCorrelationPanel from './GatewaySourceCorrelationPanel.jsx'

export default function GatewayHotspotsPanel({
  humanoidLoopHotspotsData,
  humanoidLoopHotspotTrendData,
  humanoidLoopSourceCorrelationData,
  humanoidHotspots,
  humanoidHotspotTrendLabels,
  humanoidHotspotTrendSeries,
  humanoidHotspotTrendBuckets,
  humanoidSourceItems,
}) {
  if (!humanoidLoopHotspotsData) return null

  return (
    <div className="mt-4 rounded-2xl border border-rose-200 bg-[radial-gradient(circle_at_top_left,_rgba(244,63,94,0.08),_transparent_38%),radial-gradient(circle_at_bottom_right,_rgba(249,115,22,0.08),_transparent_40%)] p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="text-xs font-semibold text-gray-600">病灶热区 / 器官聚类</div>
          <div className="mt-1 text-sm text-gray-700">
            {humanoidLoopHotspotsData?.summary || '当前窗口尚未形成系统级病灶热区。'}
          </div>
        </div>
        <span className="rounded-full border border-white/80 bg-white/90 px-2.5 py-1 text-xs font-medium text-rose-700 shadow-sm">
          {`hotspots ${humanoidLoopHotspotsData?.hotspot_count ?? humanoidHotspots.length}`}
        </span>
      </div>

      <GatewayHotspotTrendPanel
        trendData={humanoidLoopHotspotTrendData}
        labels={humanoidHotspotTrendLabels}
        series={humanoidHotspotTrendSeries}
        buckets={humanoidHotspotTrendBuckets}
      />

      <GatewaySourceCorrelationPanel
        data={humanoidLoopSourceCorrelationData}
        items={humanoidSourceItems}
      />

      <GatewayHotspotCards hotspots={humanoidHotspots} />
    </div>
  )
}
